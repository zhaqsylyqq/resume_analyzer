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
