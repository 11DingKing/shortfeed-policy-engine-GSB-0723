"""Common service response types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..events import DomainEvent
from ..reason_codes import ReasonCode


@dataclass(slots=True)
class ServiceResponse:
    ok: bool
    reason: ReasonCode
    data: dict[str, Any] = field(default_factory=dict)
    events: list[DomainEvent] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason.value, "data": self.data}
