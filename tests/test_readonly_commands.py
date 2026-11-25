"""Backups and checks never change the database (incident 2026-11-24: the scheduled
`backup-auto` ran newer code that was on the disk for a few minutes, migrated the live
database and encrypted it with a key it created itself)."""
from __future__ import annotations

import pytest

from app import READ_ONLY_COMMANDS, cli_command, create_app
from app.services import fieldcrypt, migrations, storage

from .conftest import ON_POSTGRES, TEST_DATA_KEY, make_user

WIN = r"C:\Users\abzal\Desktop\itpm\resume_analyzer\.venv\Scripts"


@pytest.mark.parametrize(("argv", "command"), [
    ([rf"{WIN}\flask.exe", "--app", "app", "backup-auto"], "backup-auto"),
    ([rf"{WIN}\Lib\site-packages\flask\__main__.py", "--app", "app", "backup-auto"], "backup-auto"),  # pythonw -m flask
    (["/usr/local/bin/flask", "-A", "app", "--debug", "db-check"], "db-check"),
    (["flask", "--app=app", "-e", "custom.env", "restore-check", "copy.pg.enc"], "restore-check"),
    (["flask", "--app", "app", "worker", "--threads", "2"], "worker"),
    (["flask", "--app", "app"], None),
    (["/usr/bin/pytest", "-q", "tests"], None),
    (["waitress-serve", "--call", "app:create_server_app"], None),
    ([], None),
])
def test_which_flask_command_runs(argv, command):
    assert cli_command(argv) == command


def test_backup_and_check_commands_are_read_only():
    assert {"backup", "backup-offsite", "backup-auto", "restore-check", "db-check", "routes"} == READ_ONLY_COMMANDS
    assert "worker" not in READ_ONLY_COMMANDS and "db-upgrade" not in READ_ONLY_COMMANDS


def _settings(tmp_path, database):
    return {"TESTING": True, "SECRET_KEY": "x", "DATABASE": database, "GEMINI_API_KEY": "k"}


def test_a_read_only_start_creates_nothing(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DATA_KEY", "")  # no key anywhere: a normal start outside production would create one
    database = str(tmp_path / "fresh.db")
    app = create_app(_settings(tmp_path, database), read_only=True)
    assert app.config["READ_ONLY_START"] is True
    assert not (data / ".data_key").exists()
    assert not (tmp_path / "fresh.db").exists()  # not even an empty database
    with pytest.raises(fieldcrypt.DataKeyError, match="not configured"):
        fieldcrypt.keyring()
    create_app(_settings(tmp_path, database))  # a normal start does both, as before
    assert (data / ".data_key").exists() and (tmp_path / "fresh.db").exists()
    fieldcrypt.configure(fieldcrypt.Keyring.from_values(TEST_DATA_KEY))


def test_the_incident_cannot_happen_again(app, monkeypatch, tmp_path):
    """Plaintext data (written by older code) stays as it is when the backup task starts newer code."""
    owner = make_user(app, "legacy")
    with app.app_context():
        db = storage.get_db()
        session_id = storage.create_session(db, "Старая", "ru", [("Python", "Python-разработчик, Django" * 3)], owner)
        with db:
            db.execute("INSERT INTO resumes (session_id, filename, sha256, text, status, created_at) "
                       "VALUES (?, 'Иванов.pdf', 'abc', 'Иванов Иван', 'done', ?)", (session_id, storage.now()))

    def must_not_run(*args, **kwargs):
        raise AssertionError("a backup start must not touch the schema or the data")

    monkeypatch.setattr(migrations, "upgrade", must_not_run)
    monkeypatch.setattr(storage, "seal_all", must_not_run)
    monkeypatch.setattr("app.retention._run", must_not_run)
    monkeypatch.setattr("sys.argv", [rf"{WIN}\flask.exe", "--app", "app", "backup-auto"])
    backup_app = create_app({**_settings(tmp_path, app.config["DATABASE"]), "DATABASE": app.config["DATABASE"]})
    assert backup_app.config["READ_ONLY_START"] is True
    with backup_app.app_context():
        assert storage.get_db().execute("SELECT text FROM resumes WHERE filename = 'Иванов.pdf'").fetchone()[0] == "Иванов Иван"
    if not ON_POSTGRES:  # the copy itself still works (PostgreSQL copies need pg_dump: test_datamove)
        result = backup_app.test_cli_runner().invoke(args=["backup", "--out", str(tmp_path / "copies")])
        assert result.exit_code == 0 and list((tmp_path / "copies").glob("app-*.db")), result.output
    result = backup_app.test_cli_runner().invoke(args=["db-check"])
    assert result.exit_code == 0 and "resumes 1" in result.output, result.output


@pytest.mark.skipif(not ON_POSTGRES, reason="needs TEST_DATABASE_URL (PostgreSQL)")
def test_db_check_says_when_the_database_and_the_code_differ(app, monkeypatch):
    result = app.test_cli_runner().invoke(args=["db-check"])
    assert result.exit_code == 0 and f"schema {migrations.head()}" in result.output and "expects" not in result.output
    monkeypatch.setattr(migrations, "current", lambda url: "0003_rate_limits")
    result = app.test_cli_runner().invoke(args=["db-check"])
    assert "schema 0003_rate_limits (this code expects 0005_sealed_demographics" in result.output


def test_head_is_the_newest_migration():
    assert migrations.head() == "0005_sealed_demographics"
