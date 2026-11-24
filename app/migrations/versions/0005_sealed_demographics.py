"""SEC-02b: demographics are stored encrypted (text)

Revision ID: 0005_sealed_demographics
Revises: 0004_audit_append_only
Create Date: 2026-11-24

`demographics.age` becomes TEXT so it can hold the encrypted value (`enc1:…`); the values
themselves are encrypted by the app at start-up (storage.seal_all), which holds the key —
migrations never see it. Downgrade keeps only ages that are still plain numbers.
"""
from __future__ import annotations

from alembic import op

revision = "0005_sealed_demographics"
down_revision = "0004_audit_append_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE demographics ALTER COLUMN age TYPE TEXT USING age::text")


def downgrade() -> None:
    op.execute("ALTER TABLE demographics ALTER COLUMN age TYPE INTEGER "
               "USING CASE WHEN age ~ '^[0-9]+$' THEN age::integer END")
