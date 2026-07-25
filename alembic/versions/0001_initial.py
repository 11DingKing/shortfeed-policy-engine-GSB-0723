"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-24

Creates the full schema: policies, sessions, per-day usage, heartbeats and the
transactional outbox. The single-active-session guard is added as a partial
unique index in a separate migration so it can be layered on with zero downtime.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("document", sa.JSON, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "version", name="uq_policies_tenant_version"),
    )
    op.create_index("ix_policies_tenant_active", "policies", ["tenant_id", "is_active"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("watched_seconds_marker", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_watched_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_sessions_tenant_idem"),
    )
    op.create_index("ix_sessions_tenant_user", "sessions", ["tenant_id", "user_id"])

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

    op.create_table(
        "heartbeats",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("watched_seconds_total", sa.Integer, nullable=False),
        sa.Column("credited_seconds", sa.Integer, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "seq", name="uq_heartbeat_session_seq"),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_outbox_unpublished", "outbox", ["published", "id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_unpublished", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("heartbeats")
    op.drop_table("session_daily_usage")
    op.drop_index("ix_sessions_tenant_user", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_policies_tenant_active", table_name="policies")
    op.drop_table("policies")
