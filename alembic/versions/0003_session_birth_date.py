"""add session birth_date

Revision ID: 0003_session_birth_date
Revises: 0002_active_session_guard
Create Date: 2026-07-24

Adds the ``birth_date`` column to ``sessions`` so age can be derived dynamically
at evaluation time. This is a zero-downtime, additive change: the column is added
with a server default so existing rows are backfilled without a table rewrite on
PostgreSQL, and old code that does not read the column continues to work.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_session_birth_date"
down_revision: str | None = "0002_active_session_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add with a server default to backfill existing rows, then drop the default
    # so future inserts must supply a real birth date (enforced by the app).
    op.add_column(
        "sessions",
        sa.Column(
            "birth_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("'1970-01-01'"),
        ),
    )
    with op.batch_alter_table("sessions") as batch:
        batch.alter_column("birth_date", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("birth_date")
