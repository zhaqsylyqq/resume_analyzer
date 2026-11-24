"""R1 — consistent backup of the SQLite database.

Uses the SQLite online backup API, so the copy is consistent even while the app is writing
(WAL mode). The copy is checked with PRAGMA integrity_check, and old backups beyond --keep
are removed.

    python scripts/backup_db.py                      # instance/app.db -> instance/backups/
    python scripts/backup_db.py --out D:\\backups --keep 30

Exit code 0 on success, 1 on any error (missing database, failed check).
Same as `flask --app app backup` (APP-09); kept for existing scheduled tasks.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.backup import PREFIX, backup  # noqa: E402,F401  (logic lives in the app package, APP-09)

# APP-01: DATA_DIR moves the database out of instance/
DATA = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else ROOT / "instance"
DEFAULT_DB = DATA / "app.db"
DEFAULT_OUT = DATA / "backups"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the Enbek Screen SQLite database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"database file (default: {DEFAULT_DB})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"backup folder (default: {DEFAULT_OUT})")
    parser.add_argument("--keep", type=int, default=14, help="how many backups to keep; 0 keeps all (default: 14)")
    args = parser.parse_args(argv)
    try:
        path = backup(args.db, args.out, args.keep)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backup written: {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
