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
from datetime import datetime
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


def _age_key(path: Path) -> tuple[str, int]:
    match = re.fullmatch(rf"{PREFIX}(\d{{8}}-\d{{6}})(?:-(\d+))?", path.name[: -len(SUFFIX)])
    return (match.group(1), int(match.group(2) or 1)) if match else ("", 0)


def list_backups(out_dir: Path) -> list[Path]:
    """Encrypted copies, oldest first."""
    return sorted(out_dir.glob(f"{PREFIX}*{SUFFIX}"), key=_age_key)


def backup_offsite(db_path: Path, out_dir: Path, key: bytes, keep: int = 30, now: datetime | None = None) -> Path:
    if not out_dir.is_dir():
        raise BackupError(f"backup folder not found (is the drive connected?): {out_dir}")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{PREFIX}{stamp}{SUFFIX}"
    n = 2
    while target.exists():
        target = out_dir / f"{PREFIX}{stamp}-{n}{SUFFIX}"
        n += 1

    # The plain snapshot stays next to the database (same disk, same permissions).
    with tempfile.TemporaryDirectory(dir=db_path.parent, prefix=".offsite-") as tmp:
        snapshot = backup(db_path, Path(tmp), keep=0, now=now)
        blob = encrypt(snapshot.read_bytes(), key)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(blob)
    os.replace(partial, target)  # a crash never leaves a half-written file under the final name

    if keep > 0:
        for old in list_backups(out_dir)[:-keep]:
            old.unlink(missing_ok=True)
    return target


def check(blob_path: Path, key: bytes, restore_to: Path | None = None) -> dict[str, int]:
    data = decrypt(blob_path.read_bytes(), key)
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
