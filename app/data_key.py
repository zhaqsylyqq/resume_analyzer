"""`python -m app.data_key` — the SEC-02b data key, without starting the app.

    python -m app.data_key init       create <data folder>/.data_key once (refuses to replace one)
    python -m app.data_key generate   print a new key (for DATA_KEY / DATA_KEY_FILE, or rotation)
    python -m app.data_key status     where the key comes from and its id (never the key itself)

Losing the key means losing the encrypted personal data: keep a copy in the password manager
shared by two people, never next to the backups.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import ConfigError, load_settings
from .services import fieldcrypt


def data_folder(data_dir: str) -> Path:
    return Path(data_dir) if data_dir else Path(__file__).resolve().parent.parent / "instance"


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "status"
    if command == "generate":
        print(fieldcrypt.generate_key())
        return 0
    load_dotenv()
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc)
        return 1
    folder = data_folder(settings.data_dir)
    path = folder / fieldcrypt.KEY_FILE
    if command == "init":
        if settings.data_key:
            print("DATA_KEY is set: nothing to create.")
            return 0
        if path.exists() and path.read_text(encoding="utf-8").strip():
            print(f"{path} already exists: kept as it is.")
            return 0
        folder.mkdir(parents=True, exist_ok=True)
        fieldcrypt.load_key_file(str(folder), create=True)
        if os.name != "nt":
            path.chmod(0o600)
        key = fieldcrypt.parse_key(path.read_text(encoding="utf-8"))
        print(f"Created {path} (key id {fieldcrypt.key_id(key)}). Copy this file's line into the password manager now.")
        return 0
    if command == "status":
        if settings.data_key:
            source = "DATA_KEY" + (" (from DATA_KEY_FILE)" if settings.secret_sources.get("DATA_KEY") else "")
            key = fieldcrypt.parse_key(settings.data_key)
        elif path.exists():
            source, key = str(path), fieldcrypt.parse_key(path.read_text(encoding="utf-8"))
        else:
            print(f"No data key yet (DATA_KEY not set, no {path}).")
            return 1
        old = [fieldcrypt.key_id(fieldcrypt.parse_key(v)) for v in settings.data_key_old.split(",") if v.strip()]
        print(f"Data key {fieldcrypt.key_id(key)} from {source}" + (f"; old keys: {', '.join(old)}" if old else ""))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
