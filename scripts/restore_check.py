"""QW-01 — prove that an encrypted backup can be restored.

Decrypts a copy made by scripts/backup_offsite.py into a temporary file, runs
PRAGMA integrity_check and prints how many users, sessions and resumes it holds. With
--restore-to it also writes the decrypted database there (the target must not exist).

    python scripts/restore_check.py                      # newest copy in BACKUP_OFFSITE_DIR
    python scripts/restore_check.py E:\\hrscreen-backups\\app-20261117-030000.db.enc
    python scripts/restore_check.py --restore-to instance\\app.db   # with the app stopped

Exit code 0 when the copy is readable and intact, 1 otherwise.
Same as `flask --app app restore-check` (APP-09); kept for the scheduled task.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_offsite  # noqa: E402  (same folder)

from app.services.backup import TABLES, check  # noqa: E402,F401  (logic lives in the app package, APP-09)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check (and optionally restore) an encrypted backup.")
    parser.add_argument("file", type=Path, nargs="?", help="encrypted copy (default: newest in BACKUP_OFFSITE_DIR)")
    parser.add_argument("--restore-to", type=Path, default=None, help="also write the decrypted database here")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(backup_offsite.ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass
    try:
        path = args.file
        if path is None:
            folder = os.environ.get("BACKUP_OFFSITE_DIR")
            copies = backup_offsite.list_backups(Path(folder)) if folder else []
            if not copies:
                raise backup_offsite.BackupError("no encrypted copies found (set BACKUP_OFFSITE_DIR or pass a file)")
            path = copies[-1]
        counts = check(path, backup_offsite.load_key(os.environ.get("BACKUP_KEY")), args.restore_to)
    except (backup_offsite.BackupError, OSError, sqlite3.Error) as exc:
        print(f"Restore check failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path.name}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))  # ASCII: Windows logs
    if args.restore_to:
        print(f"Restored to {args.restore_to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
