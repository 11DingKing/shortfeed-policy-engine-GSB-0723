"""Shared DDL for constraints that ORM ``create_all`` cannot express.

The single-active-session guarantee relies on a *partial* unique index —
``UNIQUE (tenant_id, user_id) WHERE status != 'ENDED'`` — so that a user may
accumulate many ended sessions but never two concurrent live ones. Both
PostgreSQL and modern SQLite support partial indexes with identical syntax, so
one statement serves migrations and the in-memory test bootstrap alike.
"""

from __future__ import annotations

ACTIVE_SESSION_INDEX = "uq_sessions_single_active"

CREATE_ACTIVE_SESSION_INDEX = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ACTIVE_SESSION_INDEX} "
    "ON sessions (tenant_id, user_id) WHERE status != 'ENDED'"
)

DROP_ACTIVE_SESSION_INDEX = f"DROP INDEX IF EXISTS {ACTIVE_SESSION_INDEX}"
