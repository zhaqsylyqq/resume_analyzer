"""APP-06: rate-limit counters shared by all processes

Revision ID: 0003_rate_limits
Revises: 0002_resume_raw_file
Create Date: 2026-11-19
"""
from __future__ import annotations

from alembic import op

revision = "0003_rate_limits"
down_revision = "0002_resume_raw_file"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE rate_counters (
        key          TEXT NOT NULL,
        window_start BIGINT NOT NULL,
        count        INTEGER NOT NULL,
        PRIMARY KEY (key, window_start)
    )""")
    op.execute("CREATE INDEX idx_rate_counters_start ON rate_counters (window_start)")
    op.execute("""CREATE TABLE rate_locks (
        key    TEXT PRIMARY KEY,
        until  DOUBLE PRECISION NOT NULL
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rate_locks")
    op.execute("DROP TABLE IF EXISTS rate_counters")
