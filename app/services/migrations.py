"""APP-02: PostgreSQL schema migrations (Alembic).

`upgrade(url)` brings the database to the newest revision. It runs at start-up (and as
`flask --app app db-upgrade`) under a PostgreSQL advisory lock, so several copies of the app
starting at once (Kubernetes) apply each migration exactly once. New revisions go to
app/migrations/versions/ (`alembic revision -m "..."` with alembic.ini in the project root).
SQLite keeps its built-in schema (storage.SCHEMA) until the data has moved (APP-03).
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

SCRIPT_LOCATION = str(Path(__file__).resolve().parent.parent / "migrations")
LOCK_ID = 7_270_001  # arbitrary, constant: one lock for "schema migration of this app"


def sqlalchemy_url(url: str) -> str:
    """postgresql://… -> postgresql+psycopg://… (SQLAlchemy needs the driver name)."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    raise ValueError("not a PostgreSQL URL")


def config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    engine = create_engine(sqlalchemy_url(url), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})
            try:
                cfg = config(url)
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, revision)
                conn.commit()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
                conn.commit()
    finally:
        engine.dispose()


def current(url: str) -> str | None:
    engine = create_engine(sqlalchemy_url(url))
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT to_regclass('alembic_version')")).scalar()
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar() if exists else None
    finally:
        engine.dispose()


class RoleError(RuntimeError):
    pass


def restrict_app_role(owner_url: str, role: str) -> list[str]:
    """SEC-03: least privileges for the role the app signs in with. Run as the owner of the
    tables: the role gets SELECT/INSERT/UPDATE/DELETE on the tables, but only SELECT and INSERT
    on audit_log and only SELECT on alembic_version; tables of later migrations get the same
    rights by default. Refuses the owner itself and superusers (rights do not limit them).
    Returns the statements run."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(owner_url, autocommit=False) as conn:
        found = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if found is None:
            raise RoleError(f"role {role!r} does not exist: create it first (CREATE ROLE … LOGIN PASSWORD …)")
        if found[0]:
            raise RoleError(f"role {role!r} is a superuser: no privilege limits it; use an ordinary role")
        owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public' "
                             "AND tablename = 'audit_log'").fetchone()
        if owner is None:
            raise RoleError("no audit_log table: run db-upgrade first")
        if owner[0] == role:
            raise RoleError(f"role {role!r} owns the tables: the app must sign in as another role "
                            "(the owner keeps every right on its tables)")
        ident = sql.Identifier(role)
        statements = [
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident),
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(ident),
            sql.SQL("REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON audit_log FROM {}").format(ident),
            sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM {}").format(ident),
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(ident),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE "
                    "ON TABLES TO {}").format(ident),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(ident),
        ]
        for statement in statements:
            conn.execute(statement)
        conn.commit()
        return [statement.as_string(conn) for statement in statements]
