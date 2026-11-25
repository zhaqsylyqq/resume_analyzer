"""APP-05: uploaded file kept until a worker parses it

Revision ID: 0002_resume_raw_file
Revises: 0001_initial
Create Date: 2026-11-19
"""
from __future__ import annotations

from alembic import op

revision = "0002_resume_raw_file"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE resumes ADD COLUMN raw_file BYTEA")


def downgrade() -> None:
    op.execute("ALTER TABLE resumes DROP COLUMN raw_file")
