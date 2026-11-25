"""Database backups, encrypted offsite copies and restore checks (R1, QW-01, APP-09).

The logic used by both the service commands (`flask --app app backup`, `backup-offsite`,
`restore-check`) and the older scripts in scripts/, which now only parse arguments.

- backup(): consistent SQLite snapshot (online backup API, safe while the app writes),
  checked with PRAGMA integrity_check; old snapshots beyond `keep` are removed.
- backup_offsite(): the same snapshot encrypted with AES-256-GCM into a folder outside the
  host; the plain snapshot never leaves the database's folder.
- check(): decrypt a copy into a temporary file, check it and count the main tables.
"""
from __future__ import annotations

import base64
import os
import re
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "app-"
TABLES = ("users", "sessions", "vacancies", "resumes", "scores", "audit_log")


def _snapshot_age_key(path: Path) -> tuple[str, int]:
    """Oldest first: by time stamp, then by the -2, -3 suffix of same-second backups."""
    match = re.fullmatch(rf"{PREFIX}(\d{{8}}-\d{{6}})(?:-(\d+))?", path.stem)
    return (match.group(1), int(match.group(2) or 1)) if match else ("", 0)


def backup(db_path: Path, out_dir: Path, keep: int = 14, now: datetime | None = None) -> Path:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    out_dir.mkdir(mode=0o750, parents=True, exist_ok=True)  # backups hold personal data
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{PREFIX}{stamp}.db"
    n = 2
    while target.exists():  # two backups in the same second
        target = out_dir / f"{PREFIX}{stamp}-{n}.db"
        n += 1

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        with dest:
            source.backup(dest)
        result = dest.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dest.close()
        source.close()
    if result != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"integrity check failed: {result}")

    if keep > 0:
        backups = sorted(out_dir.glob(f"{PREFIX}*.db"), key=_snapshot_age_key)
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
    return target


MAGIC = b"HRSB1\n"  # file format version; also authenticated as associated data
NONCE_BYTES = 12
SUFFIX = ".db.enc"


class BackupError(Exception):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def load_key(value: str | None) -> bytes:
    if not value:
        raise BackupError("BACKUP_KEY is not set (create one with --generate-key)")
    try:
        key = base64.urlsafe_b64decode(value.strip().encode())
    except (ValueError, TypeError) as exc:
        raise BackupError("BACKUP_KEY is not valid base64") from exc
    if len(key) != 32:
        raise BackupError("BACKUP_KEY must be 32 bytes (use --generate-key)")
    return key


def encrypt(data: bytes, key: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise BackupError("not an encrypted backup of this format")
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + NONCE_BYTES:], MAGIC)
    except Exception as exc:  # noqa: BLE001 - InvalidTag: wrong key or damaged file
        raise BackupError("cannot decrypt: wrong BACKUP_KEY or damaged file") from exc


PG_SUFFIX = ".pg.enc"  # APP-03: encrypted pg_dump (custom format) of the PostgreSQL database


def _age_key(path: Path) -> tuple[str, int]:
    stem = path.name[: -len(PG_SUFFIX)] if path.name.endswith(PG_SUFFIX) else path.name[: -len(SUFFIX)]
    match = re.fullmatch(rf"{PREFIX}(\d{{8}}-\d{{6}})(?:-(\d+))?", stem)
    return (match.group(1), int(match.group(2) or 1)) if match else ("", 0)


def list_backups(out_dir: Path) -> list[Path]:
    """Encrypted copies (SQLite and PostgreSQL), oldest first."""
    copies = list(out_dir.glob(f"{PREFIX}*{SUFFIX}")) + list(out_dir.glob(f"{PREFIX}*{PG_SUFFIX}"))
    return sorted(copies, key=_age_key)


def _new_target(out_dir: Path, suffix: str, now: datetime | None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{PREFIX}{stamp}{suffix}"
    n = 2
    while target.exists():
        target = out_dir / f"{PREFIX}{stamp}-{n}{suffix}"
        n += 1
    return target


def _write_atomically(target: Path, blob: bytes, out_dir: Path, keep: int) -> Path:
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(blob)
    os.replace(partial, target)  # a crash never leaves a half-written file under the final name
    if keep > 0:
        for old in list_backups(out_dir)[:-keep]:
            old.unlink(missing_ok=True)
    return target


def backup_offsite(db_path: Path, out_dir: Path, key: bytes, keep: int = 30, now: datetime | None = None) -> Path:
    if not out_dir.is_dir():
        raise BackupError(f"backup folder not found (is the drive connected?): {out_dir}")
    target = _new_target(out_dir, SUFFIX, now)
    # The plain snapshot stays next to the database (same disk, same permissions).
    with tempfile.TemporaryDirectory(dir=db_path.parent, prefix=".offsite-") as tmp:
        snapshot = backup(db_path, Path(tmp), keep=0, now=now)
        blob = encrypt(snapshot.read_bytes(), key)
    return _write_atomically(target, blob, out_dir, keep)


def pg_backup_offsite(url: str, out_dir: Path, key: bytes, keep: int = 30, now: datetime | None = None,
                      work_dir: Path | None = None) -> Path:
    """APP-03: pg_dump of the PostgreSQL database, encrypted like the SQLite copies."""
    from . import pgtools

    if not out_dir.is_dir():
        raise BackupError(f"backup folder not found (is the drive connected?): {out_dir}")
    target = _new_target(out_dir, PG_SUFFIX, now)
    with tempfile.TemporaryDirectory(dir=work_dir, prefix=".offsite-") as tmp:
        dump = Path(tmp) / "app.dump"
        pgtools.dump(url, dump)
        blob = encrypt(dump.read_bytes(), key)
    return _write_atomically(target, blob, out_dir, keep)


def make_offsite_copy(source: str, out_dir: Path, key: bytes, keep: int = 30, now: datetime | None = None,
                      work_dir: Path | None = None) -> Path:
    """An encrypted copy of whichever database the app uses (SQLite file or postgresql:// URL)."""
    from .db import is_postgres_url

    if is_postgres_url(source):
        return pg_backup_offsite(source, out_dir, key, keep, now, work_dir)
    return backup_offsite(Path(source), out_dir, key, keep, now)


def check(blob_path: Path, key: bytes, restore_to: Path | None = None, restore_url: str | None = None) -> dict[str, int]:
    """Decrypt a copy and prove it is usable; row counts of the main tables.

    PostgreSQL copies are restored into `restore_url` (a scratch database that is wiped) when
    given; otherwise their table of contents is checked with pg_restore --list.
    """
    data = decrypt(blob_path.read_bytes(), key)
    if blob_path.name.endswith(PG_SUFFIX):
        from . import pgtools

        if restore_to is not None:
            raise BackupError("--restore-to is for SQLite copies; restore a PostgreSQL copy with pg_restore")
        with tempfile.TemporaryDirectory(prefix="hrscreen-restore-") as tmp:
            dump = Path(tmp) / "app.dump"
            dump.write_bytes(data)
            return pgtools.verify(dump, TABLES, restore_url)
    with tempfile.TemporaryDirectory(prefix="hrscreen-restore-") as tmp:
        plain = Path(tmp) / "app.db"
        plain.write_bytes(data)
        db = sqlite3.connect(plain)
        try:
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise BackupError(f"integrity check failed: {result}")
            counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608 - fixed names
        finally:
            db.close()
    if restore_to is not None:
        if restore_to.exists():
            raise BackupError(f"refusing to overwrite {restore_to}; move it away first")
        restore_to.write_bytes(data)
    return counts


# Automatic offsite backup when the drive is connected ---------------------------------------

LAST_OK_FILE = "last_offsite.txt"  # in <data>/backups: when the last checked copy was made


@dataclass
class AutoResult:
    """What `auto_backup` did. `remind` asks the caller to tell the user to plug the drive in."""

    status: str  # "done", "fresh", "absent", "failed"
    message: str
    folder: Path | None = None
    last_ok: datetime | None = None
    remind: bool = False


def find_backup_folder(configured: Path, drives: list[Path] | None = None) -> Path | None:
    """The configured folder, or the folder with the same name on another drive root.

    Windows may give a USB drive another letter next time (F: today, E: tomorrow), so every
    drive root is checked for `<root>/<folder name>`.
    """
    if configured.is_dir():
        return configured
    if drives is None:
        drives = [Path(f"{letter}:\\") for letter in "DEFGHIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else []
    for root in drives:
        candidate = root / configured.name
        try:
            if candidate.is_dir():
                return candidate
        except OSError:  # an empty card reader and the like
            continue
    return None


def read_last_ok(state_dir: Path) -> datetime | None:
    try:
        return datetime.fromisoformat((state_dir / LAST_OK_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_last_ok(state_dir: Path, when: datetime) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / LAST_OK_FILE).write_text(when.isoformat(timespec="seconds"), encoding="utf-8")


def _copy_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def auto_backup(db_path: Path | str, configured: Path, key: bytes, state_dir: Path, *, max_age: timedelta,
                remind_after: timedelta, keep: int = 30, now: datetime | None = None,
                drives: list[Path] | None = None, restore_url: str | None = None) -> AutoResult:
    """Run often (every 30 minutes): make and verify a copy only when the drive is connected and
    the newest copy is older than `max_age`; otherwise do nothing, and ask for a reminder when
    there has been no verified copy for `remind_after`."""
    now = now or datetime.now(timezone.utc)
    state_dir.mkdir(parents=True, exist_ok=True)
    last_ok = read_last_ok(state_dir)
    folder = find_backup_folder(configured, drives)
    if folder is None:
        overdue = last_ok is None or now - last_ok > remind_after
        when = last_ok.isoformat(timespec="minutes") if last_ok else "never"
        return AutoResult("absent", f"backup drive not connected; last verified copy: {when}", None, last_ok, overdue)

    copies = list_backups(folder)
    if copies:
        newest = _copy_time(copies[-1])
        if last_ok is None or newest > last_ok:
            last_ok = newest  # e.g. a copy made by hand
        from .db import is_postgres_url

        # After the move to PostgreSQL the newest copy may still be of the old SQLite file.
        same_kind = copies[-1].name.endswith(PG_SUFFIX) == is_postgres_url(str(db_path))
        if same_kind and now - newest < max_age:
            return AutoResult("fresh", f"newest copy {copies[-1].name} is recent; nothing to do", folder, last_ok)
    try:
        target = make_offsite_copy(str(db_path), folder, key, keep, now=now.astimezone(), work_dir=state_dir)
        counts = check(target, key, restore_url=restore_url)
    except (BackupError, OSError, sqlite3.Error, RuntimeError) as exc:
        overdue = last_ok is None or now - last_ok > remind_after
        return AutoResult("failed", f"offsite backup failed: {exc}", folder, last_ok, overdue)
    write_last_ok(state_dir, now)
    summary = ", ".join(f"{k} {v}" for k, v in counts.items())
    return AutoResult("done", f"copy written and verified: {target} ({summary})", folder, now)
