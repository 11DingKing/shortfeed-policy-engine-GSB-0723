"""Domain events.

State transitions emit events that are persisted transactionally to the outbox
in the same database transaction as the state change. A separate relay
(:mod:`spe.infra.outbox`) later publishes them, giving at-least-once delivery
without a distributed transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    """A fact that happened, ready to be written to the outbox."""

    event_type: str
    tenant_id: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any]
