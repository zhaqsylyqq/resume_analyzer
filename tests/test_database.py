"""APP-02: the database layer for SQLite and PostgreSQL."""
from __future__ import annotations

import threading

import pytest

from app.services import db as dbapi
from app.services import storage

from .conftest import ON_POSTGRES, TEST_DATABASE_URL

postgres_only = pytest.mark.skipif(not ON_POSTGRES, reason="needs TEST_DATABASE_URL (PostgreSQL)")


def test_placeholders_and_percent_signs_are_translated():
    assert dbapi.translate("SELECT * FROM t WHERE a = ? AND b LIKE '50%' AND c = ?") == \
        "SELECT * FROM t WHERE a = %s AND b LIKE '50%%' AND c = %s"
    assert dbapi.translate("SELECT '?' AS q, \"we?ird\" FROM t WHERE x = ?") == \
        "SELECT '?' AS q, \"we?ird\" FROM t WHERE x = %s"
    assert dbapi.translate("SELECT 10 % 3") == "SELECT 10 %% 3"


def test_rows_by_name_and_position():
    row = dbapi.Row((7, "anna"), ("id", "username"))
    assert row["id"] == 7 and row[1] == "anna" and dict(row) == {"id": 7, "username": "anna"}
    with pytest.raises(IndexError):
        row["missing"]


def test_passwords_never_shown():
    assert dbapi.redact_url("postgresql://hr:s3cret@db:5432/hrscreen") == "postgresql://hr:***@db:5432/hrscreen"
    assert dbapi.redact_url("postgresql://db/hrscreen") == "postgresql://db/hrscreen"


def test_database_url_setting():
    from app.config import ConfigError, load_settings

    s = load_settings({"DATABASE_URL": "postgresql://hr:s3cret@db/hrscreen"})
    assert s.database_url.startswith("postgresql://")
    assert dict(s.report())["DATABASE_URL"] == "postgresql://hr:***@db/hrscreen"
    assert "SQLite" in dict(load_settings({}).report())["DATABASE_URL"]
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_settings({"DATABASE_URL": "mysql://x"})


def test_create_app_uses_database_url(monkeypatch, tmp_path):
    from app import create_app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://hr:x@127.0.0.1:1/none")
    monkeypatch.setattr(storage, "init_db", lambda app: None)  # no server needed for this check
    monkeypatch.setattr("app.init_retention", lambda app: None)
    assert create_app({"TESTING": True}).config["DATABASE"] == "postgresql://hr:x@127.0.0.1:1/none"


def test_nested_transactions_commit_once(app):
    with app.app_context():
        db = storage.get_db()
        with db:
            with db:
                db.execute("INSERT INTO settings (key, value, updated_at) VALUES ('a', '1', 'x')")
            db.execute("INSERT INTO settings (key, value, updated_at) VALUES ('b', '2', 'x')")
        with pytest.raises(RuntimeError), db:
            db.execute("INSERT INTO settings (key, value, updated_at) VALUES ('c', '3', 'x')")
            raise RuntimeError("roll back")
        assert storage.get_settings(db) == {"a": "1", "b": "2"}


def test_usernames_are_unique_ignoring_case(app):
    with app.app_context():
        db = storage.get_db()
        storage.create_user(db, "Anna", "h", "recruiter")
        assert storage.get_user_by_name(db, "aNNA")["username"] == "Anna"
        with pytest.raises(dbapi.IntegrityError):
            storage.create_user(db, "anna", "h", "recruiter")


@postgres_only
def test_migrations_are_idempotent_and_reported():
    from app.services import migrations

    migrations.upgrade(TEST_DATABASE_URL)  # already at head: nothing happens
    assert migrations.current(TEST_DATABASE_URL) == "0002_resume_raw_file"


@postgres_only
def test_two_workers_never_claim_the_same_resume(app):
    with app.app_context():
        db = storage.get_db()
        session_id = storage.create_session(db, "s", "ru", [("v", "text")])
        for i in range(40):
            storage.add_resume(db, session_id, f"{i}.txt", f"h{i}", "text", storage.PENDING)
    claimed: list[list[int]] = []
    barrier = threading.Barrier(4)

    def worker():
        conn = storage.connect(app.config["DATABASE"])
        try:
            barrier.wait()
            mine = []
            while ids := storage.claim_pending(conn, session_id, 3):
                mine.extend(ids)
            claimed.append(mine)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    every = [i for ids in claimed for i in ids]
    assert len(every) == 40 and len(set(every)) == 40


@postgres_only
def test_db_upgrade_command(app):
    result = app.test_cli_runner().invoke(args=["db-upgrade"])
    assert result.exit_code == 0 and "0002_resume_raw_file" in result.output


def test_db_upgrade_on_sqlite_is_a_no_op(app):
    if ON_POSTGRES:
        pytest.skip("SQLite only")
    result = app.test_cli_runner().invoke(args=["db-upgrade"])
    assert result.exit_code == 0 and "nothing to migrate" in result.output
