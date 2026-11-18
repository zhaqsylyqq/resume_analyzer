"""APP-03: pg_dump / pg_restore for PostgreSQL backups.

The password never appears on a command line (process lists show those): it is passed in
the PGPASSWORD environment variable of the child process only. The tools are looked up in
PG_BIN_DIR, then on PATH, then in the default Windows install folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

WINDOWS_DEFAULT = Path(r"C:\Program Files\PostgreSQL\16\bin")
TIMEOUT = 600


class PgToolError(RuntimeError):
    pass


def tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    folder = os.environ.get("PG_BIN_DIR")
    candidates = [Path(folder) / exe] if folder else []
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    candidates.append(WINDOWS_DEFAULT / exe)
    for path in candidates:
        if path.is_file():
            return str(path)
    raise PgToolError(f"{name} not found: install the PostgreSQL client tools or set PG_BIN_DIR")


def split(url: str) -> tuple[str, dict[str, str]]:
    """(connection string without the password, environment with PGPASSWORD) for the tools."""
    params = conninfo_to_dict(url)
    password = params.pop("password", None)
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = str(password)
    return make_conninfo(**{k: str(v) for k, v in params.items()}), env


def _run(args: list[str], env: dict[str, str]) -> str:
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        raise PgToolError(f"{Path(args[0]).name} failed: {exc}") from exc
    if result.returncode != 0:
        raise PgToolError(f"{Path(args[0]).name} failed: {(result.stderr or result.stdout).strip()[:500]}")
    return result.stdout


def dump(url: str, target: Path) -> None:
    """Custom-format dump (compressed, restorable table by table), without owners and grants."""
    conninfo, env = split(url)
    _run([tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", f"--file={target}",
          f"--dbname={conninfo}"], env)


def verify(dump_file: Path, tables: Iterable[str], restore_url: str | None = None) -> dict[str, int]:
    """Prove a dump is usable: every table has its data in it; with `restore_url`, restore it into
    that scratch database (wiped first) and count the rows."""
    tables = list(tables)
    listing = _run([tool("pg_restore"), "--list", str(dump_file)], dict(os.environ))
    missing = [t for t in tables if f"TABLE DATA public {t} " not in listing]
    if missing:
        raise PgToolError(f"the dump has no data for: {', '.join(missing)}")
    if not restore_url:
        return {t: -1 for t in tables}  # -1: present, not counted (no scratch database configured)
    conninfo, env = split(restore_url)
    _run([tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges", "--exit-on-error",
          f"--dbname={conninfo}", str(dump_file)], env)
    from . import db as dbapi

    conn = dbapi.connect(restore_url)
    try:
        # Table names come from the fixed list in backup.TABLES.
        return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}  # noqa: S608
    finally:
        conn.close()
