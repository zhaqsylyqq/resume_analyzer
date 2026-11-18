"""Alembic environment: raw-SQL migrations for PostgreSQL (APP-02)."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

config = context.config


def run() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # called from app.services.migrations.upgrade (holds the advisory lock)
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = config.get_main_option("sqlalchemy.url")
    if not url:  # the `alembic` command line: take DATABASE_URL from the environment / .env
        import os

        from dotenv import load_dotenv

        from app.services.migrations import sqlalchemy_url

        load_dotenv()
        url = sqlalchemy_url(os.environ["DATABASE_URL"])
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():  # `alembic upgrade --sql`: print the SQL instead of running it
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run()
