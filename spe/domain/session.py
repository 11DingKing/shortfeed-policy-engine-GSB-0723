"""Session aggregate and state machine.

The aggregate is intentionally persistence-agnostic: it exposes pure
``decide_*`` methods that take the current time (injected) and return a
:class:`SessionDecision`.  The service layer is responsible for loading the
aggregate, applying the decision, persisting it transactionally, and emitting
outbox events.  This keeps the state machine unit-testable without a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .reason_codes import ReasonCode


class SessionState(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


@dataclass(frozen=True, slots=True)
class SessionDecision:
    """Outcome of a state-machine transition.

    ``accepted`` is True when the transition produced a new state / counters.
    When False, ``reason`` explains why (duplicate heartbeat, invalid state,
    etc.) and the caller must NOT mutate the aggregate.
    """

    accepted: bool
    reason: ReasonCode
    new_state: SessionState
    elapsed_seconds: float = 0.0
    detail: str = ""


@dataclass(slots=True)
class SessionAggregate:
    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: str
    policy_document: dict
    state: SessionState
    started_at: datetime
    last_heartbeat_at: datetime
    last_resumed_at: datetime
    last_paused_at: datetime | None
    ended_at: datetime | None
    accumulated_active_seconds: float
    last_sequence: int
    user_age: int
    user_timezone: str
    approvals: frozenset[str]

    # ---- Start ----------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        id: str,
        tenant_id: str,
        user_id: str,
        policy_id: str,
        policy_version: str,
        policy_document: dict,
        now: datetime,
        user_age: int,
        user_timezone: str,
        approvals: frozenset[str] | None = None,
    ) -> SessionAggregate:
        return cls(
            id=id,
            tenant_id=tenant_id,
            user_id=user_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_document=dict(policy_document),
            state=SessionState.ACTIVE,
            started_at=now,
            last_heartbeat_at=now,
            last_resumed_at=now,
            last_paused_at=None,
            ended_at=None,
            accumulated_active_seconds=0.0,
            last_sequence=0,
            user_age=user_age,
            user_timezone=user_timezone,
            approvals=approvals or frozenset(),
        )

    # ---- Heartbeat ------------------------------------------------------
    def decide_heartbeat(self, now: datetime, seq: int) -> SessionDecision:
        if self.state is SessionState.ENDED:
            return SessionDecision(False, ReasonCode.SESSION_ALREADY_ENDED, self.state)
        if self.state is SessionState.PAUSED:
            return SessionDecision(False, ReasonCode.HEARTBEAT_PROHIBITED_WHEN_PAUSED, self.state)
        if seq < self.last_sequence:
            return SessionDecision(False, ReasonCode.HEARTBEAT_OUT_OF_ORDER, self.state)
        if seq == self.last_sequence and self.last_sequence != 0:
            return SessionDecision(False, ReasonCode.HEARTBEAT_DUPLICATE, self.state)
        if now < self.last_heartbeat_at:
            return SessionDecision(False, ReasonCode.HEARTBEAT_STALE, self.state)
        elapsed = (now - self.last_heartbeat_at).total_seconds()
        return SessionDecision(
            True,
            ReasonCode.HEARTBEAT_ACCEPTED,
            SessionState.ACTIVE,
            elapsed_seconds=elapsed,
        )

    def apply_heartbeat(self, now: datetime, seq: int) -> SessionDecision:
        d = self.decide_heartbeat(now, seq)
        if d.accepted:
            self.accumulated_active_seconds += d.elapsed_seconds
            self.last_heartbeat_at = now
            self.last_sequence = seq
        return d

    # ---- Pause ----------------------------------------------------------
    def decide_pause(self, now: datetime) -> SessionDecision:
        if self.state is SessionState.ENDED:
            return SessionDecision(False, ReasonCode.SESSION_ALREADY_ENDED, self.state)
        if self.state is SessionState.PAUSED:
            return SessionDecision(False, ReasonCode.SESSION_NOT_ACTIVE, self.state)
        elapsed = (now - self.last_heartbeat_at).total_seconds()
        return SessionDecision(
            True,
            ReasonCode.SESSION_PAUSED,
            SessionState.PAUSED,
            elapsed_seconds=max(0.0, elapsed),
        )

    def apply_pause(self, now: datetime) -> SessionDecision:
        d = self.decide_pause(now)
        if d.accepted:
            self.accumulated_active_seconds += d.elapsed_seconds
            self.last_heartbeat_at = now
            self.last_paused_at = now
            self.state = SessionState.PAUSED
        return d

    # ---- Resume ---------------------------------------------------------
    def decide_resume(self, now: datetime) -> SessionDecision:
        if self.state is SessionState.ENDED:
            return SessionDecision(False, ReasonCode.SESSION_ALREADY_ENDED, self.state)
        if self.state is SessionState.ACTIVE:
            return SessionDecision(False, ReasonCode.SESSION_NOT_PAUSED, self.state)
        return SessionDecision(True, ReasonCode.SESSION_RESUMED, SessionState.ACTIVE)

    def apply_resume(self, now: datetime) -> SessionDecision:
        d = self.decide_resume(now)
        if d.accepted:
            self.state = SessionState.ACTIVE
            self.last_resumed_at = now
            self.last_heartbeat_at = now
        return d

    # ---- End ------------------------------------------------------------
    def decide_end(self, now: datetime) -> SessionDecision:
        if self.state is SessionState.ENDED:
            return SessionDecision(False, ReasonCode.SESSION_ALREADY_ENDED, self.state)
        elapsed = 0.0
        if self.state is SessionState.ACTIVE:
            elapsed = (now - self.last_heartbeat_at).total_seconds()
        return SessionDecision(
            True,
            ReasonCode.SESSION_ENDED,
            SessionState.ENDED,
            elapsed_seconds=max(0.0, elapsed),
        )

    def apply_end(self, now: datetime) -> SessionDecision:
        d = self.decide_end(now)
        if d.accepted:
            if self.state is SessionState.ACTIVE:
                self.accumulated_active_seconds += d.elapsed_seconds
            self.state = SessionState.ENDED
            self.ended_at = now
            self.last_heartbeat_at = now
        return d

    # ---- Projection -----------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "last_heartbeat_at": self.last_heartbeat_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "accumulated_active_seconds": round(self.accumulated_active_seconds, 3),
            "last_sequence": self.last_sequence,
            "user_timezone": self.user_timezone,
            "user_age": self.user_age,
        }
