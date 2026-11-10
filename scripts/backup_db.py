"""R1 — consistent backup of the SQLite database.

Uses the SQLite online backup API, so the copy is consistent even while the app is writing
(WAL mode). The copy is checked with PRAGMA integrity_check, and old backups beyond --keep
are removed.

    python scripts/backup_db.py                      # instance/app.db -> instance/backups/
    python scripts/backup_db.py --out D:\\backups --keep 30

Exit code 0 on success, 1 on any error (missing database, failed check).
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "instance" / "app.db"
DEFAULT_OUT = ROOT / "instance" / "backups"
PREFIX = "app-"


def _age_key(path: Path) -> tuple[str, int]:
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
        backups = sorted(out_dir.glob(f"{PREFIX}*.db"), key=_age_key)
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
    return target


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
