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
Same as `flask --app app backup-offsite` (APP-09); kept for the scheduled task.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_db  # noqa: E402  (same folder; puts the project root on sys.path)

from app.services.backup import (  # noqa: E402,F401  (logic lives in the app package, APP-09)
    MAGIC,
    SUFFIX,
    BackupError,
    backup_offsite,
    decrypt,
    encrypt,
    generate_key,
    list_backups,
    load_key,
)


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
