"""backfill: fold session_daily_usage into daily_usage_ledger

Revision ID: 0005_backfill_ledger
Revises: 0004_expand_ledger
Create Date: 2026-07-25

Phase 2 of the zero-downtime **expand / backfill / cutover / contract** migration.

**Backfill** copies existing usage from the old per-session table into the new
per-user ledger by summing each user's per-day buckets. It still does **not** drop
the old table, so both representations remain readable throughout — old processes
keep using ``session_daily_usage`` while new processes use ``daily_usage_ledger``.

The fold is written as an idempotent upsert (``ON CONFLICT ... DO UPDATE`` to the
recomputed sum, not an increment), so re-running the backfill — or running it
after the cutover has already begun writing to the ledger — converges to the
authoritative total rather than double-counting. On SQLite (tests) the equivalent
``INSERT OR REPLACE`` is used.

**Cutover** happens *between this migration and the next*: deploy the new service
version (which reads/writes ``daily_usage_ledger``) across the fleet. Only once no
process references ``session_daily_usage`` is the contract migration (0006) run.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_backfill_ledger"
down_revision: str | None = "0004_expand_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Recompute the authoritative per-user/day total from the old per-session buckets.
_FOLD_POSTGRES = """
INSERT INTO daily_usage_ledger (tenant_id, user_id, local_day, seconds)
SELECT s.tenant_id, s.user_id, d.local_day, SUM(d.seconds)
FROM session_daily_usage d
JOIN sessions s ON s.id = d.session_id
GROUP BY s.tenant_id, s.user_id, d.local_day
ON CONFLICT (tenant_id, user_id, local_day)
DO UPDATE SET seconds = EXCLUDED.seconds
"""

# SQLite: INSERT OR REPLACE against the unique key achieves the same convergence.
_FOLD_SQLITE = """
INSERT OR REPLACE INTO daily_usage_ledger (tenant_id, user_id, local_day, seconds)
SELECT s.tenant_id, s.user_id, d.local_day, SUM(d.seconds)
FROM session_daily_usage d
JOIN sessions s ON s.id = d.session_id
GROUP BY s.tenant_id, s.user_id, d.local_day
"""


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(_FOLD_SQLITE if bind.dialect.name == "sqlite" else _FOLD_POSTGRES)


def downgrade() -> None:
    # Reverting the backfill empties the ledger; the old table is still the source
    # of truth at this revision, so no data is lost.
    op.execute("DELETE FROM daily_usage_ledger")
