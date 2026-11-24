"""SEC-03: the audit log is append-only

Revision ID: 0004_audit_append_only
Revises: 0003_rate_limits
Create Date: 2026-11-24

UPDATE, DELETE and TRUNCATE on audit_log raise an error for every role, the owner and
superusers included (they would have to drop or disable the triggers first, which only the
table owner can do). The app role can be limited further to SELECT and INSERT on the table
with `flask --app app db-app-role ROLE` (docs/specs/SEC-03-audit.md).
"""
from __future__ import annotations

from alembic import op

revision = "0004_audit_append_only"
down_revision = "0003_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE FUNCTION audit_log_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only (SEC-03): % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$""")
    op.execute("""CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()""")
    op.execute("""CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()""")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
