"""0002 expand: add nullable description_md column to policies

This is an *expand* migration: it is additive and backward compatible.
The old application (which does not know about ``description_md``) can
keep reading and writing ``policies`` rows because the column is nullable.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_expand_policies_md"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("policies") as batch:
        batch.add_column(sa.Column("description_md", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("policies") as batch:
        batch.drop_column("description_md")
