"""APP-03: pg_dump / pg_restore for PostgreSQL backups.

The password never appears on a command line (process lists show those): it is passed in
the PGPASSWORD environment variable of the child process only.

Tools of the same major version as the server are used: a newer pg_restore cannot restore
into an older server (pg_restore 17+ sends `SET transaction_timeout`, unknown to 16). They
are looked up in PG_BIN_DIR, then in the folder of the server's major version (Debian/Ubuntu
`/usr/lib/postgresql/<N>/bin`, Windows `C:\\Program Files\\PostgreSQL\\<N>\\bin`), then on
PATH, then in the default Windows install folder.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

WINDOWS_DEFAULT = Path(r"C:\Program Files\PostgreSQL\16\bin")
# Where packages put the tools of one major version; "{major}" is filled in.
VERSIONED_DIRS = ("/usr/lib/postgresql/{major}/bin", r"C:\Program Files\PostgreSQL\{major}\bin")
TIMEOUT = 600
_VERSION = re.compile(r"\(PostgreSQL\)\s+(\d+)")


class PgToolError(RuntimeError):
    pass


def tool(name: str, major: int | None = None) -> str:
    """Path of a client tool; with `major`, the tools of that server version come first."""
    exe = name + (".exe" if os.name == "nt" else "")
    folder = os.environ.get("PG_BIN_DIR")
    candidates = [Path(folder) / exe] if folder else []
    if major:
        candidates += [Path(pattern.format(major=major)) / exe for pattern in VERSIONED_DIRS]
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    candidates.append(WINDOWS_DEFAULT / exe)
    for path in candidates:
        if path.is_file():
            return str(path)
    raise PgToolError(f"{name} not found: install the PostgreSQL client tools or set PG_BIN_DIR")


def parse_major(version_output: str) -> int | None:
    """18 from "pg_restore (PostgreSQL) 18.1 (Ubuntu 18.1-1)"; None if it is not there."""
    match = _VERSION.search(version_output)
    return int(match.group(1)) if match else None


def client_major(path: str) -> int | None:
    return parse_major(_run([path, "--version"], dict(os.environ)))


def server_major(url: str) -> int:
    from . import db as dbapi

    conn = dbapi.connect(url)
    try:
        return int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    finally:
        conn.close()


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
    _run([tool("pg_dump", server_major(url)), "--format=custom", "--no-owner", "--no-privileges",
          f"--file={target}", f"--dbname={conninfo}"], env)


def verify(dump_file: Path, tables: Iterable[str], restore_url: str | None = None) -> dict[str, int]:
    """Prove a dump is usable: every table has its data in it; with `restore_url`, restore it into
    that scratch database (wiped first) and count the rows."""
    tables = list(tables)
    major = server_major(restore_url) if restore_url else None
    pg_restore = tool("pg_restore", major)
    if major is not None:
        found = client_major(pg_restore)
        if found is not None and found > major:
            raise PgToolError(f"pg_restore {found} cannot restore into PostgreSQL {major}: install the "
                              f"PostgreSQL {major} client tools or set PG_BIN_DIR to their folder")
    listing = _run([pg_restore, "--list", str(dump_file)], dict(os.environ))
    missing = [t for t in tables if f"TABLE DATA public {t} " not in listing]
    if missing:
        raise PgToolError(f"the dump has no data for: {', '.join(missing)}")
    if not restore_url:
        return {t: -1 for t in tables}  # -1: present, not counted (no scratch database configured)
    conninfo, env = split(restore_url)
    _run([pg_restore, "--clean", "--if-exists", "--no-owner", "--no-privileges", "--exit-on-error",
          f"--dbname={conninfo}", str(dump_file)], env)
    from . import db as dbapi

    conn = dbapi.connect(restore_url)
    try:
        # Table names come from the fixed list in backup.TABLES.
        return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}  # noqa: S608
    finally:
        conn.close()
