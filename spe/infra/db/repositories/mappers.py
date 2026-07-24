"""Mapping helpers between ORM rows and domain aggregates."""
from __future__ import annotations

from ....domain.session import SessionAggregate, SessionState
from ..models import SessionRow


def row_to_session(row: SessionRow) -> SessionAggregate:
    return SessionAggregate(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        policy_id=row.policy_id,
        policy_version=str(row.policy_version),
        policy_document=dict(row.policy_document),
        state=SessionState(row.state),
        started_at=row.started_at,
        last_heartbeat_at=row.last_heartbeat_at,
        last_resumed_at=row.last_resumed_at,
        last_paused_at=row.last_paused_at,
        ended_at=row.ended_at,
        accumulated_active_seconds=float(row.accumulated_active_seconds),
        last_sequence=int(row.last_sequence),
        user_age=int(row.user_age),
        user_timezone=row.user_timezone,
        approvals=frozenset(row.approvals or []),
    )


def apply_session_to_row(agg: SessionAggregate, row: SessionRow) -> None:
    row.id = agg.id
    row.tenant_id = agg.tenant_id
    row.user_id = agg.user_id
    row.policy_id = agg.policy_id
    row.policy_version = agg.policy_version
    row.policy_document = dict(agg.policy_document)
    row.state = agg.state.value
    row.started_at = agg.started_at
    row.last_heartbeat_at = agg.last_heartbeat_at
    row.last_resumed_at = agg.last_resumed_at
    row.last_paused_at = agg.last_paused_at
    row.ended_at = agg.ended_at
    row.accumulated_active_seconds = agg.accumulated_active_seconds
    row.last_sequence = agg.last_sequence
    row.user_age = agg.user_age
    row.user_timezone = agg.user_timezone
    row.approvals = list(agg.approvals)
