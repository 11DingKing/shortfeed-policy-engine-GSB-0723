"""expand: add daily_usage_ledger alongside session_daily_usage

Revision ID: 0004_expand_ledger
Revises: 0003_session_birth_date
Create Date: 2026-07-25

Phase 1 of a zero-downtime **expand / backfill / cutover / contract** migration
that moves daily watch-time from the per-session ``session_daily_usage`` table to
the authoritative per-user ``daily_usage_ledger``.

**Expand** only *adds* the new table; the old table is left completely intact and
untouched. After this migration both tables coexist, so during a rolling deploy:

* old service processes keep reading/writing ``session_daily_usage`` — unaffected;
* new service processes that read ``daily_usage_ledger`` find the table present
  (it is populated by the next migration, 0005 backfill).

No process ever hits a missing or dropped table. This migration writes no data
and is instantly reversible (drop the empty new table).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_expand_ledger"
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


def downgrade() -> None:
    op.drop_table("daily_usage_ledger")
