"""single active session partial unique index

Revision ID: 0002_active_session_guard
Revises: 0001_initial
Create Date: 2026-07-24

Adds the partial unique index that enforces "at most one non-ended session per
(tenant, user)". This is a purely additive, backward-compatible change: existing
readers and writers keep working, and the index simply begins rejecting a second
concurrent start. On PostgreSQL this would be created ``CONCURRENTLY`` in a real
deployment to avoid locking; the DDL here is dialect-portable for tests.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from spe.infra.db.ddl import CREATE_ACTIVE_SESSION_INDEX, DROP_ACTIVE_SESSION_INDEX

revision: str = "0002_active_session_guard"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(CREATE_ACTIVE_SESSION_INDEX)


def downgrade() -> None:
    op.execute(DROP_ACTIVE_SESSION_INDEX)
