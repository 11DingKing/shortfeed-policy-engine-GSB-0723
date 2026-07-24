"""Domain events written to the transactional outbox."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    POLICY_CREATED = "policy.created"
    POLICY_PUBLISHED = "policy.published"
    SESSION_STARTED = "session.started"
    SESSION_HEARTBEAT = "session.heartbeat"
    SESSION_PAUSED = "session.paused"
    SESSION_RESUMED = "session.resumed"
    SESSION_ENDED = "session.ended"
    SESSION_DENIED = "session.denied"
    QUOTA_HIT = "quota.hit"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: EventType
    aggregate_id: str
    tenant_id: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
