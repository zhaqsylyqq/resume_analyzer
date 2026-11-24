"""APP-03: moving the data from SQLite to PostgreSQL, and PostgreSQL backups."""
from __future__ import annotations

import os
import shutil

import pytest

from app import create_app
from app.services import backup as backups
from app.services import datamove, storage
from app.services.gemini import GeminiService

from .conftest import ON_POSTGRES, TEST_DATABASE_URL, login, make_user
from .fakes import FakeClient, smart_handler
from .helpers import create_session, resume_text, upload

pytestmark = pytest.mark.skipif(not ON_POSTGRES, reason="needs TEST_DATABASE_URL (PostgreSQL)")
RESTORE_URL = os.environ.get("TEST_RESTORE_URL", "")
needs_tools = pytest.mark.skipif(shutil.which("pg_dump") is None and not os.environ.get("PG_BIN_DIR"),
                                 reason="pg_dump / pg_restore not installed")

OVERRIDES = {"TESTING": True, "SECRET_KEY": "test-secret", "SYNC_JOBS": True, "LLM_PROVIDER": "gemini",
             "GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "test-model", "GEMINI_FALLBACK_MODEL": "",
             "INVITE_CODE": "", "DAILY_RESUME_LIMIT": 30, "STARTER_SESSION": True}


def _csrf(client):
    import re

    html = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


@pytest.fixture()
def sqlite_app(tmp_path, app):  # `app` empties the PostgreSQL test database first
    source = create_app({**OVERRIDES, "DATABASE": str(tmp_path / "old.db")})
    source.extensions["gemini"] = GeminiService(FakeClient(handler=smart_handler()), "test-model", sleep=lambda s: None)
    make_user(source, "anna")
    make_user(source, "boss", role="admin")
    client = source.test_client()
    login(client, "anna")
    csrf = _csrf(client)
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    with source.app_context():
        db = storage.get_db()
        storage.set_candidate_status(db, storage.list_vacancies(db, session_id)[0]["id"],
                                     storage.list_resumes(db, session_id)[0]["id"], "shortlist", "звонить", None)
        storage.save_settings(db, {"daily_limit": "25"})
        storage.record_llm_use(db, 1, storage.AI_BIAS_CHECK)
        storage.update_user(db, 1, totp_secret="JBSWY3DPEHPK3PXP", totp_last_step=5, recovery_codes='["x"]')
    return source


def test_everything_arrives_and_matches(sqlite_app):
    report = datamove.move(sqlite_app.config["DATABASE"], TEST_DATABASE_URL)
    assert report.ok, report.lines()
    counts = {t.table: t.target_rows for t in report.tables}
    assert counts["users"] == 2 and counts["resumes"] == 2 and counts["scores"] == 2
    assert counts["candidate_status"] == 1 and counts["demographics"] == 2 and counts["audit_log"] > 0

    # The site works on the moved data: same login, same session, new rows get new ids.
    moved = create_app({**OVERRIDES, "DATABASE": TEST_DATABASE_URL})
    client = moved.test_client()
    assert login(client, "anna").headers["Location"].endswith("/login/verify")  # 2FA moved too
    with moved.app_context():
        db = storage.get_db()
        assert storage.get_user_by_name(db, "ANNA")["totp_last_step"] == 5
        new_user = storage.create_user(db, "carol", "h", "recruiter")
        assert new_user == 3
        assert storage.list_sessions(db)[0]["resume_count"] == 2


def test_refuses_a_target_with_data_unless_replace(sqlite_app):
    assert datamove.move(sqlite_app.config["DATABASE"], TEST_DATABASE_URL).ok
    with pytest.raises(datamove.MoveError, match="already holds"):
        datamove.move(sqlite_app.config["DATABASE"], TEST_DATABASE_URL)
    assert datamove.move(sqlite_app.config["DATABASE"], TEST_DATABASE_URL, replace=True).ok


def test_the_sqlite_file_is_not_changed(sqlite_app):
    path = sqlite_app.config["DATABASE"]
    with sqlite_app.app_context():
        before = datamove.fingerprint(storage.get_db(), "resumes")
    datamove.move(path, TEST_DATABASE_URL)
    with sqlite_app.app_context():
        assert datamove.fingerprint(storage.get_db(), "resumes") == before


def test_db_move_command(sqlite_app):
    runner = sqlite_app.test_cli_runner()
    result = runner.invoke(args=["db-move", "--to", TEST_DATABASE_URL])
    assert result.exit_code == 0, result.output
    assert "All tables match" in result.output and "OK   users: 2 -> 2" in result.output
    assert "test@" not in result.output.replace(":***@", "")  # the password is masked
    again = runner.invoke(args=["db-move", "--to", TEST_DATABASE_URL])
    assert again.exit_code == 1 and "--replace" in again.output


@needs_tools
def test_postgres_backup_is_encrypted_and_restorable(app, client, csrf, fake_gemini, tmp_path):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    key = backups.load_key(backups.generate_key())
    out = tmp_path / "usb"
    out.mkdir()
    copy = backups.make_offsite_copy(TEST_DATABASE_URL, out, key, keep=2, work_dir=tmp_path)
    assert copy.name.endswith(".pg.enc") and b"PGDMP" not in copy.read_bytes()  # encrypted
    present = backups.check(copy, key)
    assert present["resumes"] == -1  # in the dump, not counted without a scratch database
    if RESTORE_URL:
        counts = backups.check(copy, key, restore_url=RESTORE_URL)
        assert counts["resumes"] == 1 and counts["sessions"] >= 1
    with pytest.raises(backups.BackupError):
        backups.check(copy, backups.load_key(backups.generate_key()))
    for _ in range(2):
        backups.make_offsite_copy(TEST_DATABASE_URL, out, key, keep=2, work_dir=tmp_path)
    assert len(backups.list_backups(out)) == 2


@needs_tools
def test_backup_commands_on_postgres(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path / "inst"))
    out = tmp_path / "usb"
    out.mkdir()
    app.config.update(BACKUP_OFFSITE_DIR=str(out), BACKUP_KEY=backups.generate_key(),
                      BACKUP_RESTORE_URL=RESTORE_URL)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["backup"]).exit_code == 0
    assert list((tmp_path / "inst" / "backups").glob("app-*.dump"))
    made = runner.invoke(args=["backup-offsite"])
    assert made.exit_code == 0, made.output
    checked = runner.invoke(args=["restore-check"])
    assert checked.exit_code == 0 and checked.output.startswith("OK: app-"), checked.output
    monkeypatch.setattr("app.cli.notify_windows", lambda *a: True)
    auto = runner.invoke(args=["backup-auto", "--max-age-hours", "1"])
    assert auto.exit_code == 0 and auto.output.startswith(("FRESH", "DONE")), auto.output


def test_restore_url_must_not_be_the_live_database():
    from app.config import ConfigError, load_settings

    with pytest.raises(ConfigError, match="BACKUP_RESTORE_URL"):
        load_settings({"DATABASE_URL": "postgresql://a@h/x", "BACKUP_RESTORE_URL": "postgresql://a@h/x"})


@needs_tools
def test_auto_backup_makes_a_postgres_copy_after_the_move(app, tmp_path):
    from datetime import datetime, timedelta, timezone

    key = backups.load_key(backups.generate_key())
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / "app-20991231-235959.db.enc").write_bytes(b"old sqlite copy")  # newest, but of the old engine
    result = backups.auto_backup(TEST_DATABASE_URL, usb, key, tmp_path / "state", max_age=timedelta(hours=20),
                                 remind_after=timedelta(days=3), now=datetime.now(timezone.utc))
    assert result.status == "done", result.message
    assert any(p.name.endswith(".pg.enc") for p in backups.list_backups(usb))
