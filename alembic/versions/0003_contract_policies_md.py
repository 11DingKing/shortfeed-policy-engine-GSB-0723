"""0003 contract: enforce description_md is populated

By the time this migration runs, every application instance has deployed the
code added in the 0002 expand phase that always writes ``description_md``.
We can now safely make the column NOT NULL with a default, which is the
*contract* phase that finalises the schema change.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_contract_policies_md"
down_revision: Union[str, None] = "0002_expand_policies_md"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill any rows that still have NULL (defensive — new code always sets it).
    op.execute("UPDATE policies SET description_md = '' WHERE description_md IS NULL")
    with op.batch_alter_table("policies") as batch:
        batch.alter_column(
            "description_md",
            existing_type=sa.String(length=2000),
            nullable=False,
            server_default="",
        )


def downgrade() -> None:
    with op.batch_alter_table("policies") as batch:
        batch.alter_column(
            "description_md",
            existing_type=sa.String(length=2000),
            nullable=True,
            server_default=None,
        )
