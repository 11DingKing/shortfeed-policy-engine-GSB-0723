"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-15 00:00:00

This is the baseline migration.  It creates every table from the ORM metadata.
Subsequent migrations must be written in the *expand / contract* style so that
the previous version of the application keeps working while the migration runs:

* **expand** phase: add nullable columns / new tables; backfill; create indexes
  ``CONCURRENTLY``.
* **contract** phase (one deploy later): drop old columns / tables.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from spe.infra.db.base import Base

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
