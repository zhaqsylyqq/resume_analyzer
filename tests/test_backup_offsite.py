"""QW-01 — encrypted offsite backup and restore check."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.sqlite_only  # file backups of the SQLite database

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backup_offsite as offsite  # noqa: E402
import restore_check as restore  # noqa: E402


@pytest.fixture()
def database(app):
    """A real app database with one user and one session."""
    from app.services import storage

    with app.app_context():
        db = storage.get_db()
        storage.create_user(db, "keeper", "hash", "recruiter")
        storage.create_session(db, "Набор", "ru", [("v", "Python-разработчик, Django, опыт от 3 лет.")])
    return Path(app.config["DATABASE"])


@pytest.fixture()
def key():
    return offsite.load_key(offsite.generate_key())


def test_backup_is_encrypted_and_restorable(database, key, tmp_path):
    out = tmp_path / "usb"
    out.mkdir()
    target = offsite.backup_offsite(database, out, key)
    blob = target.read_bytes()
    assert blob.startswith(offsite.MAGIC)
    assert b"SQLite format 3" not in blob and "Набор".encode() not in blob
    counts = restore.check(target, key)
    assert counts["users"] == 1 and counts["sessions"] == 1


def test_plain_snapshot_is_not_left_behind(database, key, tmp_path):
    out = tmp_path / "usb"
    out.mkdir()
    def files():  # SQLite's own -wal/-shm files come and go; only our leftovers matter
        return {p.name for p in database.parent.iterdir() if not p.name.endswith(("-wal", "-shm"))}

    before = files()
    offsite.backup_offsite(database, out, key)
    assert files() == before
    assert [p.suffix for p in out.iterdir()] == [".enc"]


def test_wrong_key_or_damaged_file_is_rejected(database, key, tmp_path):
    out = tmp_path / "usb"
    out.mkdir()
    target = offsite.backup_offsite(database, out, key)
    with pytest.raises(offsite.BackupError):
        restore.check(target, offsite.load_key(offsite.generate_key()))
    damaged = bytearray(target.read_bytes())
    damaged[-1] ^= 1
    target.write_bytes(bytes(damaged))
    with pytest.raises(offsite.BackupError):
        restore.check(target, key)


def test_old_copies_are_pruned(database, key, tmp_path):
    out = tmp_path / "usb"
    out.mkdir()
    for day in range(1, 5):
        offsite.backup_offsite(database, out, key, keep=2, now=datetime(2026, 11, day, 3, 0, 0))
    assert [p.name for p in offsite.list_backups(out)] == ["app-20261103-030000.db.enc", "app-20261104-030000.db.enc"]


def test_missing_folder_fails_clearly(database, key, tmp_path):
    with pytest.raises(offsite.BackupError, match="drive connected"):
        offsite.backup_offsite(database, tmp_path / "not-plugged-in", key)


@pytest.mark.parametrize("value", [None, "", "not base64!!", "c2hvcnQ="])
def test_bad_keys_are_rejected(value):
    with pytest.raises(offsite.BackupError):
        offsite.load_key(value)


def test_restore_to_writes_a_working_database_and_never_overwrites(database, key, tmp_path):
    out = tmp_path / "usb"
    out.mkdir()
    target = offsite.backup_offsite(database, out, key)
    restored = tmp_path / "restored.db"
    restore.check(target, key, restore_to=restored)
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT username FROM users").fetchone()[0] == "keeper"
    with pytest.raises(offsite.BackupError, match="overwrite"):
        restore.check(target, key, restore_to=restored)


def test_command_line(database, tmp_path, monkeypatch, capsys):
    out = tmp_path / "usb"
    out.mkdir()
    monkeypatch.setenv("BACKUP_KEY", offsite.generate_key())
    monkeypatch.setenv("BACKUP_OFFSITE_DIR", str(out))
    assert offsite.main(["--db", str(database)]) == 0
    assert restore.main([]) == 0
    assert "OK:" in capsys.readouterr().out
    monkeypatch.delenv("BACKUP_KEY")
    monkeypatch.setattr(offsite, "ROOT", tmp_path)  # no project .env with a real key
    assert offsite.main(["--db", str(database)]) == 1
