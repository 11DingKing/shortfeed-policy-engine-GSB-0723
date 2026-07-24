"""Historical replay service.

Replays a session's lifecycle deterministically from the pinned policy document
and the durable outbox event stream.  Because each session pins the policy
*document* (not just a version id), replaying historical sessions is unaffected
by subsequently published versions — this is the ``VERSION_PINNED`` guarantee.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..events import EventType
from ..policy_ast import EvaluationContext, parse_policy
from ..policy_interpreter import evaluate
from ..reason_codes import ReasonCode
from ..repositories import RepoBundle, UnitOfWork
from ..session import SessionAggregate, SessionState
from ..timeutil import local_date_for, split_by_local_midnight
from .responses import ServiceResponse


class ReplayService:
    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def replay(self, *, tenant_id: str, session_id: str) -> ServiceResponse:
        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            session = await bundle.sessions.get(tenant_id=tenant_id, session_id=session_id)
            if session is None:
                return {"error": ReasonCode.SESSION_NOT_FOUND}
            if session.tenant_id != tenant_id:
                return {"error": ReasonCode.TENANT_ISOLATION_VIOLATION}
            events = list(
                await bundle.outbox.list_for_aggregate(
                    tenant_id=tenant_id, aggregate_id=session_id
                )
            )
            events.sort(key=lambda e: (e.occurred_at, e.payload.get("seq", 0)))
            return {"session": session, "events": events}

        loaded = await self._uow.run(_tx)
        if "error" in loaded:
            return ServiceResponse(ok=False, reason=loaded["error"])

        session: SessionAggregate = loaded["session"]
        events = loaded["events"]
        doc = parse_policy(session.policy_document)

        # Walk the event stream in order, maintaining reconstructed counters.
        replay_trace: list[dict[str, Any]] = []
        daily_buckets: dict[str, float] = {}  # local_date iso -> seconds
        total_seconds = 0.0
        state = SessionState.ACTIVE
        last_event_time: datetime | None = None

        for ev in events:
            if ev.event_type is EventType.SESSION_STARTED:
                last_event_time = ev.occurred_at
                today = local_date_for(ev.occurred_at, session.user_timezone)
                ctx = EvaluationContext(
                    user_age=session.user_age,
                    user_timezone=session.user_timezone,
                    now_utc=ev.occurred_at,
                    daily_used_seconds=int(daily_buckets.get(today.isoformat(), 0.0)),
                    session_used_seconds=int(total_seconds),
                    approvals=set(session.approvals),
                )
                result = evaluate(doc, ctx)
                replay_trace.append(
                    {
                        "event": ev.event_type.value,
                        "at": ev.occurred_at.isoformat(),
                        "state": state.value,
                        "decision": result.model_dump(mode="json"),
                    }
                )
            elif ev.event_type is EventType.SESSION_HEARTBEAT:
                elapsed = float(ev.payload.get("elapsed_seconds", 0.0))
                # Attribute elapsed to daily buckets using tz math.
                if last_event_time is not None and elapsed > 0:
                    spans = split_by_local_midnight(
                        last_event_time, ev.occurred_at, session.user_timezone
                    )
                    for sp in spans:
                        daily_buckets[sp.local_date.isoformat()] = (
                            daily_buckets.get(sp.local_date.isoformat(), 0.0) + sp.seconds
                        )
                    total_seconds += elapsed
                last_event_time = ev.occurred_at
                today = local_date_for(ev.occurred_at, session.user_timezone)
                ctx = EvaluationContext(
                    user_age=session.user_age,
                    user_timezone=session.user_timezone,
                    now_utc=ev.occurred_at,
                    daily_used_seconds=int(daily_buckets.get(today.isoformat(), 0.0)),
                    session_used_seconds=int(total_seconds),
                    approvals=set(session.approvals),
                )
                result = evaluate(doc, ctx)
                replay_trace.append(
                    {
                        "event": ev.event_type.value,
                        "at": ev.occurred_at.isoformat(),
                        "sequence": ev.payload.get("sequence"),
                        "elapsed_seconds": elapsed,
                        "state": state.value,
                        "decision": result.model_dump(mode="json"),
                    }
                )
            elif ev.event_type is EventType.SESSION_PAUSED:
                state = SessionState.PAUSED
                elapsed = float(ev.payload.get("elapsed_seconds", 0.0))
                total_seconds += elapsed
                replay_trace.append(
                    {
                        "event": ev.event_type.value,
                        "at": ev.occurred_at.isoformat(),
                        "state": state.value,
                    }
                )
            elif ev.event_type is EventType.SESSION_RESUMED:
                state = SessionState.ACTIVE
                last_event_time = ev.occurred_at
                replay_trace.append(
                    {
                        "event": ev.event_type.value,
                        "at": ev.occurred_at.isoformat(),
                        "state": state.value,
                    }
                )
            elif ev.event_type in (EventType.SESSION_ENDED, EventType.SESSION_DENIED):
                elapsed = float(ev.payload.get("elapsed_seconds", 0.0))
                if state is SessionState.ACTIVE and elapsed > 0 and last_event_time is not None:
                    for sp in split_by_local_midnight(
                        last_event_time, ev.occurred_at, session.user_timezone
                    ):
                        daily_buckets[sp.local_date.isoformat()] = (
                            daily_buckets.get(sp.local_date.isoformat(), 0.0) + sp.seconds
                        )
                state = SessionState.ENDED
                total_seconds += elapsed
                replay_trace.append(
                    {
                        "event": ev.event_type.value,
                        "at": ev.occurred_at.isoformat(),
                        "state": state.value,
                        "reason": ev.payload.get("reason"),
                    }
                )

        return ServiceResponse(
            ok=True,
            reason=ReasonCode.REPLAY_COMPLETED,
            data={
                "session_id": session.id,
                "final_state": state.value,
                "pinned_policy_version": session.policy_version,
                "total_session_seconds": round(total_seconds, 3),
                "daily_buckets": {k: round(v, 3) for k, v in sorted(daily_buckets.items())},
                "steps": replay_trace,
            },
        )
