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


def test_backup_into_default_folder(app, runner, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path))  # never the developer's real instance/
    result = runner.invoke(args=["backup"])
    assert result.exit_code == 0, result.output
    assert "Backup written:" in result.output
    assert list((Path(app.instance_path) / "backups").glob("app-*.db"))


def test_backup_reports_a_missing_database(app, runner, tmp_path):
    app.config["DATABASE"] = str(tmp_path / "missing.db")
    result = runner.invoke(args=["backup", "--out", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "backup failed: database not found" in result.output


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
    assert "integrity ok" in result.output
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
