"""Session lifecycle service.

Implements start / heartbeat / pause / resume / end and enforces the engine's
core invariants:

* **Version pinning** — a session records the policy id + version active at
  start and always evaluates against that pinned document.
* **Idempotent start** — repeating a start with the same idempotency key returns
  the original session instead of creating a second one.
* **Single active session** — a user may have at most one active/paused session;
  concurrent starts are resolved by a unique DB constraint (see the repository),
  and the loser is reported with ``REJECTED_ACTIVE_SESSION_EXISTS``.
* **Ordered, idempotent heartbeats** — each heartbeat has a sequence number and a
  cumulative watched-time marker; stale/duplicate/out-of-order beats are ignored
  and only the positive delta is credited.
* **Cross-midnight settlement** — credited watch-time is attributed to the local
  day it occurred in.

The service is transport-agnostic and depends only on domain ports.
"""

from __future__ import annotations

from spe.domain.clock import Clock
from spe.domain.events import DomainEvent
from spe.domain.ids import IdGenerator
from spe.domain.policy_interpreter import EvalContext, evaluate
from spe.domain.reason_codes import ReasonCode
from spe.domain.repositories import (
    HeartbeatSink,
    OutboxRepository,
    PolicyRepository,
    SessionRepository,
)
from spe.domain.services.responses import ActionResult
from spe.domain.session import Session, SessionStatus
from spe.domain.timeutil import local_day_key


class ActiveSessionExists(Exception):
    """Raised by the repository when the single-active-session constraint trips."""


class SessionService:
    """Orchestrates the session lifecycle over the domain ports."""

    def __init__(
        self,
        sessions: SessionRepository,
        policies: PolicyRepository,
        outbox: OutboxRepository,
        clock: Clock,
        ids: IdGenerator,
        heartbeats: HeartbeatSink | None = None,
        heartbeat_max_gap_seconds: int = 90,
    ) -> None:
        self._sessions = sessions
        self._policies = policies
        self._outbox = outbox
        self._clock = clock
        self._ids = ids
        self._heartbeats = heartbeats
        self._max_gap = heartbeat_max_gap_seconds

    # -- start ---------------------------------------------------------------

    async def start(
        self,
        tenant_id: str,
        user_id: str,
        user_age: int,
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Start a new session, pinned to the tenant's active policy version."""
        if idempotency_key is not None:
            existing = await self._sessions.get_by_idempotency_key(tenant_id, idempotency_key)
            if existing is not None:
                return ActionResult.success(
                    ReasonCode.SESSION_STARTED_IDEMPOTENT, session=existing
                )

        policy = await self._policies.get_active(tenant_id)
        if policy is None:
            return ActionResult.rejected(ReasonCode.REJECTED_POLICY_NOT_FOUND)

        now = self._clock.now()
        # Evaluate eligibility at start (age + bedtime; no usage yet).
        ctx = EvalContext(
            now=now,
            user_id=user_id,
            user_age=user_age,
            daily_usage_seconds=0,
            session_elapsed_seconds=0,
        )
        decision = evaluate(policy.document, ctx)
        if not decision.allowed:
            return ActionResult.rejected(decision.reason, trace=decision.trace)

        session = Session(
            id=self._ids.new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            policy_id=policy.id,
            policy_version=policy.version,
            status=SessionStatus.ACTIVE,
            started_at=now,
            updated_at=now,
        )
        try:
            await self._sessions.add(session)
        except ActiveSessionExists:
            # Lost a concurrent race: another start committed first.
            current = await self._sessions.get_active_for_user(tenant_id, user_id)
            return ActionResult.rejected(
                ReasonCode.REJECTED_ACTIVE_SESSION_EXISTS, session=current
            )

        if idempotency_key is not None:
            await self._sessions.save(session, idempotency_key=idempotency_key)

        await self._emit(
            "session.started",
            session,
            now,
            version=session.policy_version,
        )
        return ActionResult.success(
            ReasonCode.SESSION_STARTED, session=session, trace=decision.trace
        )

    # -- heartbeat -----------------------------------------------------------

    async def heartbeat(
        self,
        tenant_id: str,
        session_id: str,
        seq: int,
        watched_seconds_total: int,
    ) -> ActionResult:
        """Apply a heartbeat, crediting only the new, in-order watch-time delta.

        ``seq`` is a per-session monotonically increasing counter; ``watched_
        seconds_total`` is the client's cumulative watched-time marker. Duplicate
        or out-of-order beats (``seq <= last_seq``) are ignored. The credited
        delta is the growth of the cumulative marker, clamped to the configured
        maximum gap to bound the effect of client silence.
        """
        session = await self._sessions.get(tenant_id, session_id)
        if session is None:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_FOUND)
        if session.status is SessionStatus.ENDED:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_ENDED, session=session)
        if session.status is not SessionStatus.ACTIVE:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_ACTIVE, session=session)

        # Idempotent / ordered guard.
        if seq <= session.last_seq:
            return ActionResult.rejected(
                ReasonCode.HEARTBEAT_IGNORED_STALE,
                session=session,
                received_seq=seq,
                last_seq=session.last_seq,
            )

        now = self._clock.now()
        raw_delta = watched_seconds_total - session.watched_seconds_marker
        delta = max(0, min(raw_delta, self._max_gap))

        local_day = local_day_key(now, await self._policy_timezone(session))
        session.add_usage(local_day, delta)
        session.watched_seconds_marker = max(
            session.watched_seconds_marker, watched_seconds_total
        )
        session.last_seq = seq
        session.updated_at = now

        if self._heartbeats is not None:
            await self._heartbeats.record(
                tenant_id=tenant_id,
                session_id=session_id,
                seq=seq,
                watched_seconds_total=watched_seconds_total,
                credited_seconds=delta,
                occurred_at=now,
            )

        # Re-evaluate hard limits against the pinned policy after crediting.
        policy = await self._policies.get_by_id(tenant_id, session.policy_id)
        assert policy is not None  # pinned policy must exist
        ctx = EvalContext(
            now=now,
            user_id=session.user_id,
            user_age=130,  # age already cleared at start; not re-checked here
            daily_usage_seconds=session.daily_seconds(local_day),
            session_elapsed_seconds=session.total_watched_seconds,
        )
        decision = evaluate(policy.document, ctx)

        if not decision.allowed:
            # A hard usage/time limit was hit: finalise the session now.
            session.status = SessionStatus.ENDED
            session.ended_at = now
            await self._sessions.save(session)
            await self._emit(
                "session.ended",
                session,
                now,
                cause=decision.reason.value,
            )
            return ActionResult.rejected(
                ReasonCode.SESSION_ENDED_BY_LIMIT,
                session=session,
                trace=decision.trace,
                limit_reason=decision.reason.value,
            )

        await self._sessions.save(session)
        await self._emit("session.heartbeat", session, now, credited_seconds=delta)
        return ActionResult.success(
            ReasonCode.HEARTBEAT_APPLIED,
            session=session,
            trace=decision.trace,
            credited_seconds=delta,
        )

    # -- pause / resume ------------------------------------------------------

    async def pause(self, tenant_id: str, session_id: str) -> ActionResult:
        session = await self._sessions.get(tenant_id, session_id)
        if session is None:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_FOUND)
        if session.status is SessionStatus.ENDED:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_ENDED, session=session)
        if session.status is not SessionStatus.ACTIVE:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_ACTIVE, session=session)

        now = self._clock.now()
        session.status = SessionStatus.PAUSED
        session.updated_at = now
        await self._sessions.save(session)
        await self._emit("session.paused", session, now)
        return ActionResult.success(ReasonCode.SESSION_PAUSED, session=session)

    async def resume(self, tenant_id: str, session_id: str) -> ActionResult:
        session = await self._sessions.get(tenant_id, session_id)
        if session is None:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_FOUND)
        if session.status is SessionStatus.ENDED:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_ENDED, session=session)
        if session.status is not SessionStatus.PAUSED:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_PAUSED, session=session)

        now = self._clock.now()
        session.status = SessionStatus.ACTIVE
        session.updated_at = now
        await self._sessions.save(session)
        await self._emit("session.resumed", session, now)
        return ActionResult.success(ReasonCode.SESSION_RESUMED, session=session)

    # -- end -----------------------------------------------------------------

    async def end(self, tenant_id: str, session_id: str) -> ActionResult:
        session = await self._sessions.get(tenant_id, session_id)
        if session is None:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_FOUND)
        if session.status is SessionStatus.ENDED:
            # Ending an already-ended session is idempotent.
            return ActionResult.success(ReasonCode.SESSION_ENDED, session=session)

        now = self._clock.now()
        session.status = SessionStatus.ENDED
        session.ended_at = now
        session.updated_at = now
        await self._sessions.save(session)
        await self._emit("session.ended", session, now, cause="client")
        return ActionResult.success(ReasonCode.SESSION_ENDED, session=session)

    # -- usage query ---------------------------------------------------------

    async def get_usage(self, tenant_id: str, session_id: str) -> ActionResult:
        session = await self._sessions.get(tenant_id, session_id)
        if session is None:
            return ActionResult.rejected(ReasonCode.REJECTED_SESSION_NOT_FOUND)
        return ActionResult.success(ReasonCode.ALLOWED, session=session)

    # -- helpers -------------------------------------------------------------

    async def _policy_timezone(self, session: Session) -> str:
        policy = await self._policies.get_by_id(session.tenant_id, session.policy_id)
        assert policy is not None
        return policy.document.rules.timezone

    async def _emit(self, event_type: str, session: Session, now, **payload) -> None:
        await self._outbox.add(
            DomainEvent(
                event_type=event_type,
                tenant_id=session.tenant_id,
                aggregate_id=session.id,
                occurred_at=now,
                payload={
                    "session_id": session.id,
                    "user_id": session.user_id,
                    "status": session.status.value,
                    **payload,
                },
            )
        )
