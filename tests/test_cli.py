"""APP-09: service commands share the app's configuration."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import backup as backups
from app.services import storage

from .helpers import create_session, resume_text, upload


@pytest.fixture()
def runner(app):
    with app.app_context():
        storage.get_db()  # make sure the database file exists
    return app.test_cli_runner()


def test_commands_are_registered(app):
    names = set(app.cli.list_commands(None))
    assert {"backup", "backup-offsite", "restore-check", "db-check", "generate-backup-key",
            "config-check", "purge-old", "create-admin"} <= names


@pytest.mark.sqlite_only
def test_backup_into_default_folder(app, runner, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path))  # never the developer's real instance/
    result = runner.invoke(args=["backup"])
    assert result.exit_code == 0, result.output
    assert "Backup written:" in result.output
    assert list((Path(app.instance_path) / "backups").glob("app-*.db"))


@pytest.mark.sqlite_only
def test_backup_reports_a_missing_database(app, runner, tmp_path):
    app.config["DATABASE"] = str(tmp_path / "missing.db")
    result = runner.invoke(args=["backup", "--out", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "backup failed: database not found" in result.output


@pytest.mark.sqlite_only
def test_offsite_backup_and_restore_check(app, runner, tmp_path):
    out = tmp_path / "offsite"
    out.mkdir()
    app.config.update(BACKUP_OFFSITE_DIR=str(out), BACKUP_KEY=backups.generate_key())
    made = runner.invoke(args=["backup-offsite", "--keep", "2"])
    assert made.exit_code == 0, made.output
    assert len(backups.list_backups(out)) == 1
    checked = runner.invoke(args=["restore-check"])
    assert checked.exit_code == 0, checked.output
    assert checked.output.startswith("OK: app-") and "users " in checked.output
    restored = tmp_path / "restored.db"
    again = runner.invoke(args=["restore-check", str(backups.list_backups(out)[0]), "--restore-to", str(restored)])
    assert again.exit_code == 0 and restored.is_file()


def test_offsite_needs_folder_and_valid_key(app, runner, tmp_path):
    app.config.update(BACKUP_OFFSITE_DIR="", BACKUP_KEY=backups.generate_key())
    result = runner.invoke(args=["backup-offsite"])
    assert result.exit_code == 1 and "BACKUP_OFFSITE_DIR" in result.output
    app.config.update(BACKUP_OFFSITE_DIR=str(tmp_path), BACKUP_KEY="")
    result = runner.invoke(args=["backup-offsite"])
    assert result.exit_code == 1 and "BACKUP_KEY is not set" in result.output
    app.config["BACKUP_KEY"] = backups.generate_key()
    result = runner.invoke(args=["restore-check"])
    assert result.exit_code == 1 and "no encrypted copies found" in result.output


@pytest.mark.sqlite_only
def test_restore_check_with_the_wrong_key(app, runner, tmp_path):
    app.config.update(BACKUP_OFFSITE_DIR=str(tmp_path), BACKUP_KEY=backups.generate_key())
    assert runner.invoke(args=["backup-offsite"]).exit_code == 0
    app.config["BACKUP_KEY"] = backups.generate_key()
    result = runner.invoke(args=["restore-check"])
    assert result.exit_code == 1 and "wrong BACKUP_KEY" in result.output


def test_db_check(app, runner, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    result = runner.invoke(args=["db-check"])
    assert result.exit_code == 0, result.output
    assert "integrity ok" in result.output or "schema 0001_initial" in result.output
    assert "sessions 1" in result.output and "resumes 1" in result.output
    assert "Resumes by status: done 1" in result.output
    assert "Sessions waiting for analysis: 0" in result.output


def test_generate_backup_key(runner):
    result = runner.invoke(args=["generate-backup-key"])
    assert result.exit_code == 0
    assert len(backups.load_key(result.output.strip())) == 32


def test_backup_key_is_validated_by_the_configuration():
    from app.config import ConfigError, load_settings

    assert load_settings({"BACKUP_KEY": backups.generate_key()}).backup_key
    with pytest.raises(ConfigError, match="BACKUP_KEY"):
        load_settings({"BACKUP_KEY": "not-a-key"})
    report = dict(load_settings({"BACKUP_KEY": backups.generate_key()}).report())
    assert report["BACKUP_KEY"] == "set"


# Automatic backup when the USB drive is connected -----------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _auto(app, configured, state, **kw):
    with app.app_context():
        return backups.auto_backup(Path(app.config["DATABASE"]), configured, kw.pop("key"), state,
                                   max_age=timedelta(hours=20), remind_after=timedelta(days=3), **kw)


@pytest.mark.sqlite_only
def test_auto_backup_when_drive_is_connected_then_skips_while_fresh(app, runner, tmp_path):
    key = backups.load_key(backups.generate_key())
    drive, state = tmp_path / "usb" / "hrscreen-backups", tmp_path / "state"
    drive.mkdir(parents=True)
    first = _auto(app, drive, state, key=key, now=NOW)
    assert first.status == "done" and len(backups.list_backups(drive)) == 1
    assert backups.read_last_ok(state) == NOW
    second = _auto(app, drive, state, key=key, now=datetime.now(timezone.utc))
    assert second.status == "fresh" and len(backups.list_backups(drive)) == 1


@pytest.mark.sqlite_only
def test_auto_backup_finds_the_drive_under_another_letter(app, runner, tmp_path):
    key = backups.load_key(backups.generate_key())
    other_root = tmp_path / "E"
    (other_root / "hrscreen-backups").mkdir(parents=True)
    result = _auto(app, tmp_path / "F" / "hrscreen-backups", tmp_path / "state", key=key, now=NOW,
                   drives=[tmp_path / "D", other_root])
    assert result.status == "done" and result.folder == other_root / "hrscreen-backups"


def test_auto_backup_reminds_when_absent_too_long(app, runner, tmp_path):
    key = backups.load_key(backups.generate_key())
    state = tmp_path / "state"
    missing = tmp_path / "F" / "hrscreen-backups"
    never = _auto(app, missing, state, key=key, now=NOW, drives=[])
    assert never.status == "absent" and never.remind
    backups.write_last_ok(state, NOW - timedelta(days=1))
    recent = _auto(app, missing, state, key=key, now=NOW, drives=[])
    assert recent.status == "absent" and not recent.remind
    backups.write_last_ok(state, NOW - timedelta(days=4))
    assert _auto(app, missing, state, key=key, now=NOW, drives=[]).remind


@pytest.mark.sqlite_only
def test_auto_backup_reports_a_failure(app, runner, tmp_path):
    key = backups.load_key(backups.generate_key())
    drive = tmp_path / "usb"
    drive.mkdir()
    app.config["DATABASE"] = str(tmp_path / "missing.db")
    result = _auto(app, drive, tmp_path / "state", key=key, now=NOW)
    assert result.status == "failed" and result.remind


@pytest.mark.sqlite_only
def test_backup_auto_command_logs_and_notifies_once_a_day(app, runner, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr("app.cli.notify_windows", lambda title, text: shown.append((title, text)) or True)
    monkeypatch.setattr(app, "instance_path", str(tmp_path / "inst"))
    app.config.update(BACKUP_OFFSITE_DIR=str(tmp_path / "absent" / "hrscreen-backups"), BACKUP_KEY=backups.generate_key())
    monkeypatch.setattr(backups, "find_backup_folder", lambda configured, drives=None: None)
    first = runner.invoke(args=["backup-auto"])
    assert first.exit_code == 0 and first.output.startswith("ABSENT:")
    assert len(shown) == 1 and "флешку" in shown[0][0]
    runner.invoke(args=["backup-auto"])
    assert len(shown) == 1  # not again within a day
    log = (tmp_path / "inst" / "backups" / "offsite.log").read_text(encoding="utf-8")
    assert "reminder:" in log

    usb = tmp_path / "usb" / "hrscreen-backups"
    usb.mkdir(parents=True)
    monkeypatch.setattr(backups, "find_backup_folder", lambda configured, drives=None: usb)
    done = runner.invoke(args=["backup-auto"])
    assert done.exit_code == 0 and done.output.startswith("DONE:")
    assert "copy written and verified" in (tmp_path / "inst" / "backups" / "offsite.log").read_text(encoding="utf-8")


def test_notify_is_a_no_op_outside_windows(monkeypatch):
    from app import cli

    monkeypatch.setattr(cli.os, "name", "posix")
    assert cli.notify_windows("a", "b") is False
