"""authoritative daily usage ledger

Revision ID: 0004_daily_usage_ledger
Revises: 0003_session_birth_date
Create Date: 2026-07-24

Introduces the authoritative ``daily_usage_ledger`` keyed by
``(tenant_id, user_id, local_day)`` and retires the old per-session
``session_daily_usage`` table.

**Reversible data migration.** The two representations carry the same
information at the granularity that matters — watch-time consumed per
``(tenant, user, local_day)``:

* **upgrade** folds every session's per-day buckets into one per-user ledger row
  by ``SUM`` over the user's sessions;
* **downgrade** writes each ledger total *back* into ``session_daily_usage``,
  attributing the whole per-user/day total to a single deterministic
  representative session (the user's most recent one). This preserves the
  consumed quota so 0003-era code reads the same totals.

Because the round trip is exact at the per-user/day level, ``0003 -> 0004``,
``0004 -> 0003`` and ``0003 -> 0004`` again neither drop nor double-count usage:
each direction recreates its own target table empty and re-derives it, so nothing
accumulates across cycles.

Both steps are online-safe: a new table is created and populated before the old
one is dropped, so readers/writers of unrelated tables are never blocked.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_daily_usage_ledger"
down_revision: str | None = "0003_session_birth_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Fold per-session daily buckets into the per-user ledger (forward direction).
_FOLD_INTO_LEDGER = """
INSERT INTO daily_usage_ledger (tenant_id, user_id, local_day, seconds)
SELECT s.tenant_id, s.user_id, d.local_day, SUM(d.seconds)
FROM session_daily_usage d
JOIN sessions s ON s.id = d.session_id
GROUP BY s.tenant_id, s.user_id, d.local_day
"""

# Unfold the per-user ledger back into per-session rows, attributing each
# (tenant, user, day) total to the user's most recent session. Uses only a
# correlated scalar subquery + EXISTS, so it runs on both PostgreSQL and SQLite.
# Ledger rows whose user has no session at all are skipped (cannot exist in
# practice: usage is only ever booked while a session exists).
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

    op.execute(_FOLD_INTO_LEDGER)

    op.drop_table("session_daily_usage")


def downgrade() -> None:
    # Recreate the old per-session table, then fold the ledger totals back into it
    # so consumed quota survives the rollback and a subsequent re-upgrade.
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

    op.drop_table("daily_usage_ledger")
