"""authoritative daily usage ledger

Revision ID: 0004_daily_usage_ledger
Revises: 0003_session_birth_date
Create Date: 2026-07-24

Introduces the authoritative ``daily_usage_ledger`` keyed by
``(tenant_id, user_id, local_day)`` and retires the old per-session
``session_daily_usage`` table. Migrating the data forward *sums* each session's
per-day buckets into the per-user ledger, so a user's already-consumed daily
budget is preserved across the cutover — restarting a session cannot reset it.

Both steps are online-safe: creating a new table and folding data into it does
not block reads/writes of unrelated tables, and the drop of the now-unused table
happens last.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_daily_usage_ledger"
down_revision: str | None = "0003_session_birth_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_usage_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("local_day", sa.String(10), nullable=False),
        sa.Column("seconds", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "local_day", name="uq_ledger_tenant_user_day"
        ),
    )

    # Fold existing per-session daily usage into the per-user ledger by summing
    # over all of a user's sessions for each local day.
    op.execute(
        """
        INSERT INTO daily_usage_ledger (tenant_id, user_id, local_day, seconds)
        SELECT s.tenant_id, s.user_id, d.local_day, SUM(d.seconds)
        FROM session_daily_usage d
        JOIN sessions s ON s.id = d.session_id
        GROUP BY s.tenant_id, s.user_id, d.local_day
        """
    )

    op.drop_table("session_daily_usage")


def downgrade() -> None:
    # Recreate the old per-session table (empty); the per-user aggregation is not
    # losslessly reversible back to individual sessions, so the ledger's data is
    # left in place under the new table only.
    op.create_table(
        "session_daily_usage",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("local_day", sa.String(10), nullable=False),
        sa.Column("seconds", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("session_id", "local_day", name="uq_daily_session_day"),
    )
    op.drop_table("daily_usage_ledger")
