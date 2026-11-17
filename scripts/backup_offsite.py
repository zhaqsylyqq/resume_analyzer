"""QW-01 — encrypted copy of the database outside the host.

Makes a consistent snapshot with scripts/backup_db.py, encrypts it with AES-256-GCM and writes
it to BACKUP_OFFSITE_DIR: a folder on another disk, a USB drive, a NAS or a synced folder of
a storage provider that keeps data in Kazakhstan. The plain snapshot never leaves the
instance folder and is deleted right after encryption. Old encrypted copies beyond --keep
are removed.

    python scripts/backup_offsite.py --generate-key          # once: prints a new BACKUP_KEY
    python scripts/backup_offsite.py                         # BACKUP_OFFSITE_DIR and BACKUP_KEY from .env
    python scripts/backup_offsite.py --out E:\\hrscreen-backups --keep 30

Keep BACKUP_KEY in a password manager with two people: without it the copies cannot be read.
Exit code 0 on success, 1 on any error. Check a copy with scripts/restore_check.py.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_db  # noqa: E402  (same folder)

MAGIC = b"HRSB1\n"  # file format version; also authenticated as associated data
NONCE_BYTES = 12
SUFFIX = ".db.enc"
PREFIX = "app-"


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
        snapshot = backup_db.backup(db_path, Path(tmp), keep=0, now=now)
        blob = encrypt(snapshot.read_bytes(), key)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(blob)
    os.replace(partial, target)  # a crash never leaves a half-written file under the final name

    if keep > 0:
        for old in list_backups(out_dir)[:-keep]:
            old.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Encrypted copy of the database outside the host (QW-01).")
    parser.add_argument("--db", type=Path, default=backup_db.DEFAULT_DB, help="database file")
    parser.add_argument("--out", type=Path, default=None, help="backup folder (default: BACKUP_OFFSITE_DIR)")
    parser.add_argument("--keep", type=int, default=30, help="copies to keep; 0 keeps all (default: 30)")
    parser.add_argument("--generate-key", action="store_true", help="print a new BACKUP_KEY and exit")
    args = parser.parse_args(argv)

    if args.generate_key:
        print(generate_key())
        return 0
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover - python-dotenv is a runtime dependency
        pass
    out = args.out or (Path(os.environ["BACKUP_OFFSITE_DIR"]) if os.environ.get("BACKUP_OFFSITE_DIR") else None)
    try:
        if out is None:
            raise BackupError("set BACKUP_OFFSITE_DIR in .env or pass --out")
        target = backup_offsite(args.db, out, load_key(os.environ.get("BACKUP_KEY")), args.keep)
    except (BackupError, OSError, RuntimeError) as exc:
        print(f"Offsite backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Encrypted backup written: {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
