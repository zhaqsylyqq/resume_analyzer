"""R1 — retention purge and database backups."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from app import retention
from app.services import jobs, storage

from .helpers import VACANCY_TEXT

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backup_db  # noqa: E402


def make_session(db, title, days_old, with_data=True):
    sid = storage.create_session(db, title, "ru", [("v", VACANCY_TEXT)])
    if with_data:
        rid = storage.add_resume(db, sid, f"{title}.txt", f"h-{title}", "Текст резюме", storage.DONE)
        vid = storage.list_vacancies(db, sid)[0]["id"]
        db.execute("INSERT INTO demographics (resume_id, gender, age) VALUES (?, 'female', 30)", (rid,))
        db.execute("INSERT INTO scores (resume_id, vacancy_id, score, verdict, summary, details_json, created_at) "
                   "VALUES (?, ?, 80, 'good', 's', '{}', 'x')", (rid, vid))
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat(timespec="seconds")
    db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (stamp, sid))
    db.commit()
    return sid


def counts(db):
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("sessions", "vacancies", "resumes", "scores", "demographics")}


def test_purge_deletes_old_sessions_with_all_data_and_keeps_recent(app):
    with app.app_context():
        db = storage.get_db()
        old = make_session(db, "Старая", 120)
        recent = make_session(db, "Свежая", 10)
        assert retention.purge(db, 90) == [old]
        assert storage.get_session(db, old) is None and storage.get_session(db, recent) is not None
        assert counts(db) == {"sessions": 1, "vacancies": 1, "resumes": 1, "scores": 1, "demographics": 1}
        entry = storage.list_audit(db)[0]
        assert (entry["action"], entry["username"], entry["session_id"]) == ("session_purge", "system", old)
        assert "Старая" in entry["detail"] and "90" in entry["detail"]


def test_purge_boundary_and_zero_days(app):
    with app.app_context():
        db = storage.get_db()
        make_session(db, "89", 89, with_data=False)
        ninety_one = make_session(db, "91", 91, with_data=False)
        assert retention.purge(db, 0) == []
        assert retention.purge(db, 90) == [ninety_one]


def test_purge_skips_sessions_with_a_running_job(app, monkeypatch):
    with app.app_context():
        db = storage.get_db()
        sid = make_session(db, "Идёт анализ", 200)
        monkeypatch.setattr(jobs, "is_running", lambda session_id: session_id == sid)
        assert retention.purge(db, 90) == []
        monkeypatch.setattr(jobs, "is_running", lambda session_id: False)
        assert retention.purge(db, 90) == [sid]


def test_purge_runs_at_app_start(tmp_path):
    path = str(tmp_path / "start.db")
    first = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": path, "RETENTION_DAYS": 0})
    with first.app_context():
        sid = make_session(storage.get_db(), "Старая", 400)
    second = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": path, "RETENTION_DAYS": 30})
    with second.app_context():
        assert storage.get_session(storage.get_db(), sid) is None


def test_purge_runs_again_on_a_request_after_a_day(app, client, monkeypatch):
    with app.app_context():
        sid = make_session(storage.get_db(), "Старая", 400)
    client.get("/healthz")  # less than a day since the start-up run: nothing happens
    with app.app_context():
        assert storage.get_session(storage.get_db(), sid) is not None
    app.extensions["retention_last_run"] -= retention.RUN_INTERVAL + 1
    client.get("/healthz")
    with app.app_context():
        assert storage.get_session(storage.get_db(), sid) is None


def test_purge_old_cli(app):
    with app.app_context():
        db = storage.get_db()
        make_session(db, "a", 100, with_data=False)
        make_session(db, "b", 40, with_data=False)
    runner = app.test_cli_runner()
    assert "Deleted 1 session(s) not changed for 90 days." in runner.invoke(args=["purge-old"]).output
    assert "Deleted 1 session(s) not changed for 30 days." in runner.invoke(args=["purge-old", "--days", "30"]).output
    assert "Retention is off" in runner.invoke(args=["purge-old", "--days", "0"]).output


def test_retention_setting_from_environment(monkeypatch):
    from app.config import settings_from_env

    monkeypatch.setenv("RETENTION_DAYS", "30")
    assert settings_from_env()["RETENTION_DAYS"] == 30
    monkeypatch.setenv("RETENTION_DAYS", "-5")
    assert settings_from_env()["RETENTION_DAYS"] == 0
    monkeypatch.delenv("RETENTION_DAYS")
    assert settings_from_env()["RETENTION_DAYS"] == 90


# Backups ---------------------------------------------------------------------------------

@pytest.fixture()
def live_db(app):
    with app.app_context():
        make_session(storage.get_db(), "Для бэкапа", 1)
    return Path(app.config["DATABASE"])


def test_backup_is_consistent_while_another_connection_writes(live_db, tmp_path):
    writer = storage.connect(str(live_db))  # WAL mode, uncommitted transaction open
    writer.execute("BEGIN")
    writer.execute("INSERT INTO sessions (title, language, created_at, updated_at) VALUES ('черновик', 'ru', 'x', 'x')")
    path = backup_db.backup(live_db, tmp_path / "out", keep=5, now=datetime(2026, 11, 10, 7, 30, 0))
    writer.rollback()
    writer.close()
    assert path.name == "app-20261110-073000.db"
    copy = sqlite3.connect(path)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [r[0] for r in copy.execute("SELECT title FROM sessions")] == ["Для бэкапа"]  # no uncommitted row
    assert copy.execute("SELECT COUNT(*) FROM resumes").fetchone()[0] == 1
    copy.close()


def test_backup_rotation_keeps_the_newest(live_db, tmp_path):
    out = tmp_path / "out"
    made = [backup_db.backup(live_db, out, keep=3, now=datetime(2026, 11, d, 3, 0, 0)) for d in range(1, 6)]
    assert sorted(p.name for p in out.iterdir()) == [p.name for p in made[-3:]]
    same_second = backup_db.backup(live_db, out, keep=3, now=datetime(2026, 11, 5, 3, 0, 0))
    assert same_second.name == "app-20261105-030000-2.db"
    # The same-second copy is the newest one: rotation drops 3 November, not it.
    assert sorted(p.name for p in out.iterdir()) == sorted([made[3].name, made[4].name, same_second.name])


def test_backup_cli_reports_missing_database(tmp_path, capsys):
    assert backup_db.main(["--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "out")]) == 1
    assert "database not found" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_backup_cli_success(live_db, tmp_path, capsys):
    assert backup_db.main(["--db", str(live_db), "--out", str(tmp_path / "b"), "--keep", "2"]) == 0
    assert "Backup written:" in capsys.readouterr().out
    assert len(list((tmp_path / "b").glob("app-*.db"))) == 1
