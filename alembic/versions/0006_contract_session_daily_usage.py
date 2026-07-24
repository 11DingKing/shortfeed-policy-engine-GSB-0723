"""contract: drop the now-unused session_daily_usage table

Revision ID: 0006_contract_sdu
Revises: 0005_backfill_ledger
Create Date: 2026-07-25

Phase 4 (final) of the zero-downtime **expand / backfill / cutover / contract**
migration. Run this **only after the cutover is complete** — i.e. every service
process now reads and writes ``daily_usage_ledger`` and none references
``session_daily_usage`` any more.

**Contract** drops the retired per-session table. By this point the ledger is the
sole source of truth, so no data is lost.

**Reversibility (downgrade).** To keep the rollback data-preserving, the downgrade
recreates ``session_daily_usage`` and folds the ledger totals back into it,
attributing each ``(tenant, user, local_day)`` total to the user's most recent
session. This restores exactly the information older code paths expect, so a fleet
that rolls ``0006 -> 0005`` finds both tables present and populated again — the
expand/backfill state — without dropping consumed quota.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_contract_sdu"
down_revision: str | None = "0005_backfill_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Unfold the per-user ledger back into per-session rows, attributing each
# (tenant, user, day) total to the user's most recent session. Portable across
# PostgreSQL and SQLite (correlated scalar subquery + EXISTS).
_UNFOLD_FROM_LEDGER = """
INSERT INTO session_daily_usage (session_id, tenant_id, local_day, seconds)
SELECT
    (SELECT s.id FROM sessions s
       WHERE s.tenant_id = l.tenant_id AND s.user_id = l.user_id
       ORDER BY s.started_at DESC, s.id
       LIMIT 1) AS session_id,
    l.tenant_id, l.local_day, l.seconds
FROM daily_usage_ledger l
WHERE EXISTS (
    SELECT 1 FROM sessions s
    WHERE s.tenant_id = l.tenant_id AND s.user_id = l.user_id
)
"""


def upgrade() -> None:
    op.drop_table("session_daily_usage")


def downgrade() -> None:
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
    op.execute(_UNFOLD_FROM_LEDGER)
