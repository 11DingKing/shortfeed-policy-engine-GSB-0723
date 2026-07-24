from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.enums import ReasonCode, SessionAction, SessionStatus
from app.domain.session.events import DomainEvent, EventRecorder


@dataclass
class SessionAggregate:
    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    last_heartbeat_seq: int
    paused_at: datetime | None
    total_active_seconds: int
    user_timezone: str
    user_age: int | None
    last_evaluation_reason: str | None
    last_evaluation_detail: dict[str, Any] | None
    idempotency_key: str | None
    _recorder: EventRecorder = field(default_factory=EventRecorder, repr=False)
    _event_seq: int = field(default=0, repr=False)

    @staticmethod
    def create(
        session_id: str,
        tenant_id: str,
        user_id: str,
        policy_id: str,
        policy_version: int,
        now: datetime,
        user_age: int | None,
        user_timezone: str,
        idempotency_key: str | None,
    ) -> SessionAggregate:
        session = SessionAggregate(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            policy_id=policy_id,
            policy_version=policy_version,
            status=SessionStatus.ACTIVE,
            started_at=now,
            ended_at=None,
            last_heartbeat_at=now,
            last_heartbeat_seq=0,
            paused_at=None,
            total_active_seconds=0,
            user_timezone=user_timezone,
            user_age=user_age,
            last_evaluation_reason=None,
            last_evaluation_detail=None,
            idempotency_key=idempotency_key,
        )
        session._record_event(SessionAction.START, ReasonCode.OK, {"started_at": now.isoformat()})
        return session

    def _record_event(self, action: SessionAction, reason: ReasonCode, detail: dict[str, Any]) -> None:
        self._event_seq += 1
        event = DomainEvent(
            event_id=f"{self.id}-{self._event_seq}",
            session_id=self.id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            action=action,
            reason_code=reason,
            detail=detail,
            occurred_at=datetime.now(timezone.utc),
        )
        self._recorder.record(event)

    @property
    def events(self) -> list[DomainEvent]:
        return self._recorder._events

    def drain_events(self) -> list[DomainEvent]:
        return self._recorder.drain()

    def apply_evaluation_result(
        self,
        allowed: bool,
        reason_code: ReasonCode,
        detail: dict[str, Any],
        now: datetime,
    ) -> ReasonCode:
        self.last_evaluation_reason = reason_code.value
        self.last_evaluation_detail = detail
        if not allowed:
            if self.status == SessionStatus.ACTIVE:
                self.status = SessionStatus.EXPIRED
                self.ended_at = now
                self._accumulate_active_time(now)
                self._record_event(
                    SessionAction.END, reason_code,
                    {"ended_at": now.isoformat(), "forced": True, **detail},
                )
                return reason_code
            return reason_code
        return ReasonCode.OK

    def heartbeat(self, seq: int, now: datetime) -> tuple[bool, ReasonCode]:
        if self.status in (SessionStatus.ENDED, SessionStatus.EXPIRED):
            return False, ReasonCode.SESSION_ALREADY_ENDED
        if seq < self.last_heartbeat_seq:
            return False, ReasonCode.HEARTBEAT_OUT_OF_ORDER
        if seq == self.last_heartbeat_seq and self.last_heartbeat_at is not None:
            return False, ReasonCode.DUPLICATE_HEARTBEAT
        if self.status == SessionStatus.PAUSED:
            return False, ReasonCode.SESSION_NOT_ACTIVE
        prev_hb = self.last_heartbeat_at or self.started_at
        delta = int((now - prev_hb).total_seconds())
        if delta > 0:
            self.total_active_seconds += delta
        self.last_heartbeat_at = now
        self.last_heartbeat_seq = seq
        self._record_event(SessionAction.HEARTBEAT, ReasonCode.OK, {
            "seq": seq, "delta_seconds": delta, "total_active_seconds": self.total_active_seconds,
        })
        return True, ReasonCode.OK

    def pause(self, now: datetime) -> tuple[bool, ReasonCode]:
        if self.status in (SessionStatus.ENDED, SessionStatus.EXPIRED):
            return False, ReasonCode.SESSION_ALREADY_ENDED
        if self.status == SessionStatus.PAUSED:
            return False, ReasonCode.SESSION_ALREADY_PAUSED
        self._accumulate_active_time(now)
        self.status = SessionStatus.PAUSED
        self.paused_at = now
        self._record_event(SessionAction.PAUSE, ReasonCode.OK, {"paused_at": now.isoformat()})
        return True, ReasonCode.OK

    def resume(self, now: datetime) -> tuple[bool, ReasonCode]:
        if self.status in (SessionStatus.ENDED, SessionStatus.EXPIRED):
            return False, ReasonCode.SESSION_ALREADY_ENDED
        if self.status != SessionStatus.PAUSED:
            return False, ReasonCode.SESSION_NOT_PAUSED
        self.status = SessionStatus.ACTIVE
        self.last_heartbeat_at = now
        self.paused_at = None
        self._record_event(SessionAction.RESUME, ReasonCode.OK, {"resumed_at": now.isoformat()})
        return True, ReasonCode.OK

    def end(self, now: datetime, reason: str | None = None) -> tuple[bool, ReasonCode]:
        if self.status in (SessionStatus.ENDED, SessionStatus.EXPIRED):
            return False, ReasonCode.SESSION_ALREADY_ENDED
        self._accumulate_active_time(now)
        self.status = SessionStatus.ENDED
        self.ended_at = now
        detail: dict[str, Any] = {"ended_at": now.isoformat(), "forced": False}
        if reason:
            detail["reason"] = reason
        self._record_event(SessionAction.END, ReasonCode.OK, detail)
        return True, ReasonCode.OK

    def _accumulate_active_time(self, now: datetime) -> None:
        if self.status == SessionStatus.ACTIVE:
            prev = self.last_heartbeat_at or self.started_at
            delta = int((now - prev).total_seconds())
            if delta > 0:
                self.total_active_seconds += delta
            self.last_heartbeat_at = now

    @property
    def active_duration_seconds(self) -> int:
        return self.total_active_seconds

    @staticmethod
    def from_db_row(row: Any) -> SessionAggregate:
        return SessionAggregate(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            policy_id=row.policy_id,
            policy_version=row.policy_version,
            status=SessionStatus(row.status),
            started_at=row.started_at,
            ended_at=row.ended_at,
            last_heartbeat_at=row.last_heartbeat_at,
            last_heartbeat_seq=row.last_heartbeat_seq,
            paused_at=row.paused_at,
            total_active_seconds=row.total_active_seconds,
            user_timezone=row.user_timezone,
            user_age=row.user_age,
            last_evaluation_reason=row.last_evaluation_reason,
            last_evaluation_detail=row.last_evaluation_detail,
            idempotency_key=row.idempotency_key,
        )
