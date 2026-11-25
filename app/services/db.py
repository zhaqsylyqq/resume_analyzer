"""APP-02: one small database layer for SQLite and PostgreSQL.

The rest of the app writes plain SQL with `?` placeholders and reads rows by column name
(`row["id"]`) or position (`row[0]`), as it did with sqlite3. `connect()` returns a
`Database` that behaves the same on both engines:

- `DATABASE` (Flask config) is a file path -> SQLite (development, tests, the laptop until the
  data is moved); `postgresql://…` -> PostgreSQL (production).
- `?` becomes `%s` for psycopg; `%` in SQL text is escaped.
- `with db:` is one transaction (commit on success, rollback on error) on both engines.
- `db.dialect` is "sqlite" or "postgresql" for the few queries that differ (see `sql_*`).
- `IntegrityError` / `DatabaseError` catch the errors of either driver.

Schema: SQLite keeps the built-in schema in storage.py; PostgreSQL is managed by Alembic
(migrations/), applied at start-up under an advisory lock or with `flask --app app db-upgrade`.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

try:  # psycopg is only needed for PostgreSQL
    import psycopg
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - it is in requirements.txt
    psycopg = None  # type: ignore[assignment]
    pg_errors = None  # type: ignore[assignment]

SQLITE = "sqlite"
POSTGRESQL = "postgresql"

IntegrityError: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,) + (
    (psycopg.IntegrityError,) if psycopg else ()
)
DatabaseError: tuple[type[BaseException], ...] = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())


def is_postgres_url(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


def redact_url(url: str) -> str:
    """postgresql://user:secret@host/db -> postgresql://user:***@host/db (for logs and reports)."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    credentials, host = rest.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}" if ":" in credentials else url


def translate(sql: str) -> str:
    """sqlite3-style `?` placeholders -> psycopg `%s`; literal `%` -> `%%`. Quoted text is kept."""
    out: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class Row(tuple):
    """A result row readable by position and by column name, like sqlite3.Row."""

    _names: tuple[str, ...] = ()

    def __new__(cls, values: Sequence[Any], names: tuple[str, ...]) -> Row:
        row = super().__new__(cls, values)
        row._names = names
        return row

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._names.index(key))
            except ValueError:
                raise IndexError(f"No item with that key: {key!r}") from None
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._names)


def _row_factory(cursor: Any) -> Any:
    names = tuple(d.name for d in cursor.description) if cursor.description else ()

    def make(values: Sequence[Any]) -> Row:
        return Row(values, names)

    return make


class Cursor:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def rowcount(self) -> int:
        return int(self._raw.rowcount)

    def fetchone(self) -> Any:
        return self._raw.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._raw.fetchall())

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())


class Database:
    """A connection to SQLite or PostgreSQL with the sqlite3 calling style."""

    def __init__(self, raw: Any, dialect: str) -> None:
        self.raw = raw
        self.dialect = dialect
        self._depth = 0

    @property
    def is_postgres(self) -> bool:
        return self.dialect == POSTGRESQL

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        if self.dialect == SQLITE:
            return Cursor(self.raw.execute(sql, tuple(params)))
        cursor = self.raw.cursor()
        cursor.execute(translate(sql), tuple(params))
        return Cursor(cursor)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        if self.dialect == SQLITE:
            self.raw.executemany(sql, [tuple(r) for r in rows])
            return
        with self.raw.cursor() as cursor:
            cursor.executemany(translate(sql), [tuple(r) for r in rows])

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> Database:
        self._depth += 1
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        self._depth -= 1
        if self._depth:
            return  # nested `with db:` blocks commit once, at the outermost level
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()


def connect(target: str) -> Database:
    """`target` is a SQLite file path or a postgresql:// URL."""
    raw: Any
    if is_postgres_url(target):
        if psycopg is None:  # pragma: no cover
            raise RuntimeError("psycopg is not installed")
        raw = psycopg.connect(target, row_factory=_row_factory, connect_timeout=10)
        return Database(raw, POSTGRESQL)
    raw = sqlite3.connect(target, timeout=30, detect_types=0, check_same_thread=True)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("PRAGMA busy_timeout = 30000")
    return Database(raw, SQLITE)


# Dialect-specific SQL fragments ----------------------------------------------------------------

def sql_local_day(dialect: str, column: str, utc_offset: int) -> str:
    """'YYYY-MM-DD' of an ISO timestamp column shifted by `utc_offset` hours (int, not user text)."""
    offset = int(utc_offset)
    if dialect == POSTGRESQL:
        return f"to_char(({column})::timestamptz AT TIME ZONE 'UTC' + interval '{offset} hours', 'YYYY-MM-DD')"
    return f"date({column}, '{offset:+d} hours')"
