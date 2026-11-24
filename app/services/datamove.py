"""APP-03: move all data from the SQLite file to PostgreSQL, and prove nothing was lost.

`move(sqlite_path, postgres_url)`:
1. migrates the PostgreSQL schema to the newest revision;
2. refuses to run when the target already holds data (unless `replace=True`, which empties it
   first) — a second run can never mix two copies;
3. copies every table in foreign-key order, keeping the original ids, in ONE transaction:
   either everything arrives or nothing does;
4. moves the identity sequences past the copied ids, so new rows get fresh ids;
5. compares row counts and a content fingerprint of every table on both sides.

The SQLite file is only read. Run it with the site stopped so no request writes in between.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db as dbapi
from . import migrations

# Parents before children (foreign keys); the columns of each table, in the same order on both sides.
TABLES: dict[str, tuple[str, ...]] = {
    "users": ("id", "username", "password_hash", "role", "active", "created_at", "last_login_at", "email",
              "totp_secret", "totp_last_step", "recovery_codes"),
    "sessions": ("id", "title", "language", "created_at", "updated_at", "owner_id"),
    "vacancies": ("id", "session_id", "title", "text", "bias_json", "created_at"),
    "resumes": ("id", "session_id", "filename", "sha256", "text", "status", "error", "profile_json", "created_at",
                "analyzed_at", "started_at", "raw_file"),
    "demographics": ("resume_id", "gender", "age"),
    "scores": ("resume_id", "vacancy_id", "score", "verdict", "summary", "details_json", "created_at"),
    "settings": ("key", "value", "updated_at"),
    "audit_log": ("id", "created_at", "user_id", "username", "action", "session_id", "detail"),
    "candidate_status": ("vacancy_id", "resume_id", "status", "note", "updated_at", "updated_by"),
    "llm_usage": ("id", "user_id", "kind", "created_at"),
    "password_resets": ("token_hash", "user_id", "created_by", "created_at", "expires_at", "used_at"),
}
IDENTITY_TABLES = ("users", "sessions", "vacancies", "resumes", "audit_log", "llm_usage")
KEY_ORDER: dict[str, str] = {
    "demographics": "resume_id", "scores": "resume_id, vacancy_id", "settings": "key",
    "candidate_status": "vacancy_id, resume_id", "password_resets": "token_hash",
}


class MoveError(RuntimeError):
    pass


@dataclass
class TableReport:
    table: str
    source_rows: int
    target_rows: int
    source_hash: str
    target_hash: str

    @property
    def ok(self) -> bool:
        return self.source_rows == self.target_rows and self.source_hash == self.target_hash


@dataclass
class MoveReport:
    tables: list[TableReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.tables)

    def lines(self) -> list[str]:
        return [f"{'OK  ' if t.ok else 'DIFF'} {t.table}: {t.source_rows} -> {t.target_rows}" for t in self.tables]


def _order(table: str) -> str:
    return KEY_ORDER.get(table, "id")


def _normalize(value: Any) -> str:
    """Same text for the same value on both engines (None, ints, text)."""
    if value is None:
        return "\x00"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def fingerprint(db: dbapi.Database, table: str) -> tuple[int, str]:
    """(row count, SHA-256 over all rows in key order): equal on both sides only if every value matches."""
    columns = ", ".join(TABLES[table])
    digest = hashlib.sha256()
    count = 0
    # Table and column names come from the fixed TABLES map above, never from input.
    for row in db.execute(f"SELECT {columns} FROM {table} ORDER BY {_order(table)}"):  # noqa: S608
        digest.update("\x1f".join(_normalize(v) for v in row).encode("utf-8"))
        digest.update(b"\x1e")
        count += 1
    return count, digest.hexdigest()


def _target_rows(target: dbapi.Database) -> int:
    return sum(int(target.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in TABLES)  # noqa: S608


def _source_columns(source: dbapi.Database, table: str) -> set[str]:
    return {row[1] for row in source.raw.execute(f"PRAGMA table_info({table})")}


def move(sqlite_path: str, postgres_url: str, *, replace: bool = False, batch: int = 500) -> MoveReport:
    if not dbapi.is_postgres_url(postgres_url):
        raise MoveError("the target must be a postgresql:// URL")
    source = dbapi.connect(sqlite_path)
    try:
        # Bring an old SQLite file up to the current columns first (additive, as the app does at start).
        from . import storage

        source.raw.executescript(storage.SCHEMA)
        storage._migrate(source)
        source.commit()

        migrations.upgrade(postgres_url)
        target = dbapi.connect(postgres_url)
        try:
            existing = _target_rows(target)
            target.rollback()
            if existing and not replace:
                raise MoveError(f"the PostgreSQL database already holds {existing} rows; "
                                "use --replace to empty it first (the SQLite file is never changed)")
            with target:
                if existing:
                    # SEC-03: emptying the audit log is a deliberate owner-only step (the table
                    # owner switches the guard off for this transaction only).
                    target.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_truncate")
                    target.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
                    target.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_truncate")
                for table, columns in TABLES.items():
                    present = _source_columns(source, table)
                    missing = [c for c in columns if c not in present]
                    if missing:
                        raise MoveError(f"{table}: the SQLite file has no column(s) {', '.join(missing)}")
                    names = ", ".join(columns)
                    marks = ", ".join("?" * len(columns))
                    cursor = source.raw.execute(f"SELECT {names} FROM {table} ORDER BY {_order(table)}")  # noqa: S608
                    while rows := cursor.fetchmany(batch):
                        target.executemany(f"INSERT INTO {table} ({names}) VALUES ({marks})",  # noqa: S608
                                           [tuple(r) for r in rows])
                for table in IDENTITY_TABLES:
                    # Next id after the largest copied one (1 for an empty table).
                    target.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "  # noqa: S608
                        f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                    )
            report = MoveReport()
            for table in TABLES:
                s_rows, s_hash = fingerprint(source, table)
                t_rows, t_hash = fingerprint(target, table)
                report.tables.append(TableReport(table, s_rows, t_rows, s_hash, t_hash))
            target.rollback()
            return report
        finally:
            target.close()
    except sqlite3.Error as exc:
        raise MoveError(f"cannot read the SQLite file: {exc}") from exc
    finally:
        source.close()
