from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import SessionAction, ReasonCode


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    session_id: str
    tenant_id: str
    user_id: str
    action: SessionAction
    reason_code: ReasonCode
    detail: dict[str, Any]
    occurred_at: datetime


@dataclass
class EventRecorder:
    _events: list[DomainEvent] = field(default_factory=list)

    def record(self, event: DomainEvent) -> None:
        self._events.append(event)

    def drain(self) -> list[DomainEvent]:
        events = list(self._events)
        self._events.clear()
        return events
