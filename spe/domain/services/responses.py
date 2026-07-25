"""Service-level result types.

Services return rich results carrying a :class:`ReasonCode` plus any relevant
payload, rather than raising for expected outcomes. The API layer maps these to
HTTP responses. Genuine programming errors still raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from spe.domain.policy_interpreter import DecisionTrace
from spe.domain.reason_codes import ReasonCode
from spe.domain.session import Session, SessionStatus


@dataclass
class PolicyPublished:
    """Result of publishing a new policy version."""

    policy_id: str
    tenant_id: str
    version: int
    name: str


@dataclass
class PreviewResult:
    """Result of a dry-run policy preview against a hypothetical context."""

    allowed: bool
    reason: ReasonCode
    trace: list[dict[str, str | None]]


@dataclass
class SessionView:
    """A serialisable snapshot of a session for API responses."""

    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    birth_date: date
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None
    total_watched_seconds: int

    @classmethod
    def of(cls, s: Session) -> SessionView:
        return cls(
            id=s.id,
            tenant_id=s.tenant_id,
            user_id=s.user_id,
            policy_id=s.policy_id,
            policy_version=s.policy_version,
            status=s.status,
            birth_date=s.birth_date,
            started_at=s.started_at,
            updated_at=s.updated_at,
            ended_at=s.ended_at,
            total_watched_seconds=s.total_watched_seconds,
        )


@dataclass
class ActionResult:
    """The outcome of a session lifecycle action (start/heartbeat/pause/...)."""

    reason: ReasonCode
    ok: bool
    session: SessionView | None = None
    trace: list[dict[str, str | None]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        reason: ReasonCode,
        session: Session | None = None,
        trace: DecisionTrace | None = None,
        **extra: Any,
    ) -> ActionResult:
        return cls(
            reason=reason,
            ok=True,
            session=SessionView.of(session) if session else None,
            trace=trace.as_list() if trace else [],
            extra=extra,
        )

    @classmethod
    def rejected(
        cls,
        reason: ReasonCode,
        session: Session | None = None,
        trace: DecisionTrace | None = None,
        **extra: Any,
    ) -> ActionResult:
        return cls(
            reason=reason,
            ok=False,
            session=SessionView.of(session) if session else None,
            trace=trace.as_list() if trace else [],
            extra=extra,
        )
