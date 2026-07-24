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
  and only the positive delta is credited. Heartbeats take a row lock so
  concurrent beats for one session serialise.
* **Authoritative daily ledger** — daily usage lives in a per-(tenant, user,
  local-day) ledger, not the session, so restarting a session never resets a
  user's daily quota.
* **Budget-truncated crediting** — the credited delta is clamped to what remains
  of both the session limit and the daily limit *before* it is written, so usage
  is never over-recorded and then rolled back.
* **Precise cross-midnight split** — a delta that straddles local midnight is
  divided between the two local days to the second.
* **Dynamic age** — the user's age is derived from their birth date and the
  current local date at evaluation time; nothing is hard-coded.

The service is transport-agnostic and depends only on domain ports. All writes
in a single call happen in one unit of work (one DB transaction), so the session
mutation, ledger increment, heartbeat record, idempotency key and outbox event
commit atomically.
"""

from __future__ import annotations

from datetime import date, datetime

from spe.domain.clock import Clock
from spe.domain.events import DomainEvent
from spe.domain.ids import IdGenerator
from spe.domain.policy_ast import PolicyDocument
from spe.domain.policy_interpreter import EvalContext, evaluate
from spe.domain.reason_codes import ReasonCode
from spe.domain.repositories import (
    DailyUsageLedger,
    HeartbeatSink,
    OutboxRepository,
    PolicyRepository,
    SessionRepository,
)
from spe.domain.services.responses import ActionResult
from spe.domain.session import Session, SessionStatus
from spe.domain.timeutil import age_at, local_day_key, split_watch_window


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
        ledger: DailyUsageLedger,
        heartbeats: HeartbeatSink | None = None,
        heartbeat_max_gap_seconds: int = 90,
    ) -> None:
        self._sessions = sessions
        self._policies = policies
        self._outbox = outbox
        self._clock = clock
        self._ids = ids
        self._ledger = ledger
        self._heartbeats = heartbeats
        self._max_gap = heartbeat_max_gap_seconds

    # -- start ---------------------------------------------------------------

    async def start(
        self,
        tenant_id: str,
        user_id: str,
        birth_date: date,
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
        tz = policy.document.rules.timezone
        # Evaluate eligibility at start against the authoritative daily ledger
        # (age + bedtime + already-consumed daily budget; no session usage yet).
        local_day = local_day_key(now, tz)
        daily_used = await self._ledger.get_seconds(tenant_id, user_id, local_day)
        ctx = EvalContext(
            now=now,
            user_id=user_id,
            user_age=age_at(birth_date, now, tz),
            daily_usage_seconds=daily_used,
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
            birth_date=birth_date,
            started_at=now,
            updated_at=now,
        )
        try:
            await self._sessions.add(session, idempotency_key=idempotency_key)
        except ActiveSessionExists:
            # Lost a concurrent race: another start committed first.
            current = await self._sessions.get_active_for_user(tenant_id, user_id)
            return ActionResult.rejected(
                ReasonCode.REJECTED_ACTIVE_SESSION_EXISTS, session=current
            )

        await self._emit("session.started", session, now, version=session.policy_version)
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
        """Apply a heartbeat, crediting only the new, in-order, budget-bounded delta.

        ``seq`` is a per-session monotonically increasing counter; ``watched_
        seconds_total`` is the client's cumulative watched-time marker. Duplicate
        or out-of-order beats (``seq <= last_seq``) are ignored. The proposed
        delta (growth of the cumulative marker, clamped to the max gap) is split
        across any local-midnight boundary and then truncated to what remains of
        both the session and daily limits, so nothing is ever over-credited.
        """
        session = await self._sessions.get_for_update(tenant_id, session_id)
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
        policy = await self._policies.get_by_id(tenant_id, session.policy_id)
        assert policy is not None  # pinned policy must exist
        tz = policy.document.rules.timezone

        proposed = max(
            0, min(watched_seconds_total - session.watched_seconds_marker, self._max_gap)
        )

        credited, per_day, hit_limit, limit_reason = await self._credit(
            session, policy.document, tz, now, proposed
        )

        # Advance the marker/seq regardless of how much was creditable, so the
        # cumulative accounting stays monotonic and idempotent.
        session.watched_seconds_marker = max(session.watched_seconds_marker, watched_seconds_total)
        session.last_seq = seq
        session.updated_at = now

        if self._heartbeats is not None:
            await self._heartbeats.record(
                tenant_id=tenant_id,
                session_id=session_id,
                seq=seq,
                watched_seconds_total=watched_seconds_total,
                credited_seconds=credited,
                occurred_at=now,
            )

        if hit_limit:
            session.status = SessionStatus.ENDED
            session.ended_at = now
            await self._sessions.save(session)
            await self._emit("session.ended", session, now, cause=limit_reason)
            return ActionResult.rejected(
                ReasonCode.SESSION_ENDED_BY_LIMIT,
                session=session,
                credited_seconds=credited,
                per_day=per_day,
                limit_reason=limit_reason,
            )

        await self._sessions.save(session)
        await self._emit("session.heartbeat", session, now, credited_seconds=credited)
        return ActionResult.success(
            ReasonCode.HEARTBEAT_APPLIED,
            session=session,
            credited_seconds=credited,
            per_day=per_day,
        )

    async def _credit(
        self,
        session: Session,
        document: PolicyDocument,
        timezone: str,
        now: datetime,
        proposed: int,
    ) -> tuple[int, dict[str, int], bool, str | None]:
        """Credit up to ``proposed`` seconds, bounded by session & daily budgets.

        Returns ``(credited, per_day_breakdown, hit_limit, limit_reason)``. The
        interval is split across local midnight and each segment is truncated to
        the remaining session budget and that day's remaining daily budget before
        being written to the ledger and the session total.
        """
        session_cap = (
            document.rules.session_limit.max_seconds
            if document.rules.session_limit
            else None
        )
        daily_cap = (
            document.rules.daily_limit.max_seconds if document.rules.daily_limit else None
        )

        credited = 0
        per_day: dict[str, int] = {}
        hit_limit = False
        limit_reason: str | None = None

        for day, segment_seconds in split_watch_window(now, proposed, timezone):
            allow = segment_seconds

            # Bound by remaining session budget.
            if session_cap is not None:
                session_remaining = session_cap - session.total_watched_seconds
                if allow >= session_remaining:
                    allow = max(0, session_remaining)
                    hit_limit = True
                    limit_reason = ReasonCode.DENIED_SESSION_LIMIT_REACHED.value

            # Bound by remaining daily budget for this specific local day.
            if daily_cap is not None:
                already = await self._ledger.get_seconds(session.tenant_id, session.user_id, day)
                day_remaining = daily_cap - (already + per_day.get(day, 0))
                if allow >= day_remaining:
                    allow = max(0, day_remaining)
                    hit_limit = True
                    limit_reason = ReasonCode.DENIED_DAILY_LIMIT_REACHED.value

            if allow > 0:
                await self._ledger.add_seconds(session.tenant_id, session.user_id, day, allow)
                session.total_watched_seconds += allow
                credited += allow
                per_day[day] = per_day.get(day, 0) + allow

            if hit_limit:
                break

        return credited, per_day, hit_limit, limit_reason

    # -- pause / resume ------------------------------------------------------

    async def pause(self, tenant_id: str, session_id: str) -> ActionResult:
        session = await self._sessions.get_for_update(tenant_id, session_id)
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
        session = await self._sessions.get_for_update(tenant_id, session_id)
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
        session = await self._sessions.get_for_update(tenant_id, session_id)
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
        # Report the authoritative daily total for the session's current local day.
        policy = await self._policies.get_by_id(tenant_id, session.policy_id)
        daily_today = 0
        if policy is not None:
            day = local_day_key(self._clock.now(), policy.document.rules.timezone)
            daily_today = await self._ledger.get_seconds(tenant_id, session.user_id, day)
        return ActionResult.success(
            ReasonCode.ALLOWED, session=session, daily_today_seconds=daily_today
        )

    # -- helpers -------------------------------------------------------------

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
