"""Session application service.

Orchestrates the session state machine against the persistence and policy
layers.  Every public method is an async transaction that:

1. Checks idempotency.
2. Loads the aggregate / policy rows.
3. Applies a pure state-machine decision.
4. Persists changes and writes outbox events atomically.

Time and identifiers are injected so tests — and the replay engine — can run
deterministically.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..clock import Clock
from ..events import DomainEvent, EventType
from ..ids import IdGenerator
from ..policy_ast import EvaluationContext, parse_policy
from ..policy_interpreter import evaluate
from ..reason_codes import ReasonCode
from ..repositories import RepoBundle, UnitOfWork
from ..session import SessionAggregate, SessionState
from ..timeutil import local_date_for, split_by_local_midnight
from .responses import ServiceResponse


class SessionService:
    def __init__(self, *, uow: UnitOfWork, clock: Clock, id_gen: IdGenerator) -> None:
        self._uow = uow
        self._clock = clock
        self._id_gen = id_gen

    # ------------------------------------------------------------------ start
    async def start(
        self,
        *,
        tenant_id: str,
        user_id: str,
        policy_id: str,
        idempotency_key: str,
        user_age: int,
        user_timezone: str,
        approvals: Iterable[str] | None = None,
        initial_sequence: int = 0,
    ) -> ServiceResponse:
        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            # Idempotency: same key → replay stored response.
            existing = await bundle.idempotency.get(
                tenant_id=tenant_id, idempotency_key=idempotency_key
            )
            if existing and existing.get("request_type") == "session.start":
                return {"replay": existing["response"], "replayed": True}

            now = self._clock.now()
            cur = await bundle.policies.get_current(tenant_id=tenant_id, policy_id=policy_id)
            if cur is None:
                return {"error": ReasonCode.POLICY_NOT_FOUND}

            doc = parse_policy(cur.document)
            today = local_date_for(now, user_timezone)
            daily_used = await bundle.usage.get_daily(
                tenant_id=tenant_id, user_id=user_id, local_date=today
            )
            ctx = EvaluationContext(
                user_age=user_age,
                user_timezone=user_timezone,
                now_utc=now,
                daily_used_seconds=int(daily_used),
                session_used_seconds=0,
                approvals=set(approvals or ()),
            )
            decision = evaluate(doc, ctx)
            if not decision.allowed:
                resp = {
                    "allowed": False,
                    "reason": decision.reason,
                    "rule_id": decision.rule_id,
                    "trace": [t.model_dump(mode="json") for t in decision.trace],
                }
                await bundle.idempotency.store(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    request_type="session.start",
                    request_hash="",
                    response=resp,
                    now=now,
                )
                await bundle.outbox.add(
                    DomainEvent(
                        event_type=EventType.SESSION_DENIED,
                        aggregate_id=user_id,
                        tenant_id=tenant_id,
                        occurred_at=now,
                        payload={"reason": decision.reason, "rule_id": decision.rule_id},
                    )
                )
                return {"ok": False, "response": resp, "replayed": False}

            # Prevent double-active sessions using the DB constraint.
            existing_active = await bundle.sessions.get_active_for_user(
                tenant_id=tenant_id, user_id=user_id
            )
            if existing_active is not None:
                return {"error": ReasonCode.CONCURRENT_SESSION_BLOCKED,
                        "existing_session_id": existing_active.id}

            session_id = self._id_gen.new_id()
            session = SessionAggregate.create(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                policy_id=policy_id,
                policy_version=str(cur.version),
                policy_document=cur.document,
                now=now,
                user_age=user_age,
                user_timezone=user_timezone,
                approvals=frozenset(approvals or ()),
            )
            session.last_sequence = initial_sequence
            try:
                await bundle.sessions.insert(session)
            except Exception as exc:  # IntegrityError raised by the driver
                if _is_unique_violation(exc):
                    return {"error": ReasonCode.CONCURRENT_SESSION_BLOCKED}
                raise

            resp = {
                "session_id": session_id,
                "state": session.state.value,
                "policy_version": session.policy_version,
                "started_at": now.isoformat(),
                "reason": ReasonCode.SESSION_STARTED.value,
            }
            await bundle.idempotency.store(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                request_type="session.start",
                request_hash="",
                response=resp,
                now=now,
            )
            await bundle.outbox.add(
                DomainEvent(
                    event_type=EventType.SESSION_STARTED,
                    aggregate_id=session_id,
                    tenant_id=tenant_id,
                    occurred_at=now,
                    payload={
                        "user_id": user_id,
                        "policy_id": policy_id,
                        "policy_version": session.policy_version,
                    },
                    idempotency_key=idempotency_key,
                )
            )
            return {"ok": True, "response": resp, "replayed": False}

        result = await self._uow.run(_tx)
        if result.get("replayed"):
            return ServiceResponse(
                ok=result["replay"].get("allowed", True),
                reason=ReasonCode.IDEMPOTENT_REPLAY,
                data=result["replay"],
            )
        if "error" in result:
            data = {}
            if "existing_session_id" in result:
                data["existing_session_id"] = result["existing_session_id"]
            return ServiceResponse(ok=False, reason=result["error"], data=data)
        return ServiceResponse(
            ok=result["ok"],
            reason=(
                ReasonCode.SESSION_STARTED if result["ok"] else ReasonCode(result["response"]["reason"])
            ),
            data=result["response"],
        )

    # --------------------------------------------------------------- heartbeat
    async def heartbeat(
        self,
        *,
        tenant_id: str,
        session_id: str,
        sequence: int,
        idempotency_key: str | None = None,
    ) -> ServiceResponse:
        idem = idempotency_key or f"hb:{tenant_id}:{session_id}:{sequence}"

        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            existing = await bundle.idempotency.get(tenant_id=tenant_id, idempotency_key=idem)
            if existing and existing.get("request_type") == "session.heartbeat":
                return {"replay": existing["response"], "replayed": True}

            session = await bundle.sessions.get(tenant_id=tenant_id, session_id=session_id)
            if session is None:
                return {"error": ReasonCode.SESSION_NOT_FOUND}
            if session.tenant_id != tenant_id:
                return {"error": ReasonCode.TENANT_ISOLATION_VIOLATION}

            now = self._clock.now()
            prev_heartbeat = session.last_heartbeat_at
            decision = session.apply_heartbeat(now, sequence)
            resp: dict[str, Any]
            events: list[DomainEvent] = []

            if not decision.accepted:
                # Duplicate / out-of-order / stale are *soft* — return current state.
                soft = {
                    ReasonCode.HEARTBEAT_OUT_OF_ORDER,
                    ReasonCode.HEARTBEAT_DUPLICATE,
                    ReasonCode.HEARTBEAT_STALE,
                }
                resp = {
                    "session_id": session.id,
                    "state": session.state.value,
                    "reason": decision.reason.value,
                    "accumulated_active_seconds": round(session.accumulated_active_seconds, 3),
                }
                await bundle.idempotency.store(
                    tenant_id=tenant_id,
                    idempotency_key=idem,
                    request_type="session.heartbeat",
                    request_hash=str(sequence),
                    response=resp,
                    now=now,
                )
                return {
                    "ok": decision.reason in soft,
                    "response": resp,
                    "replayed": False,
                    "soft": decision.reason in soft,
                }

            # Attribute elapsed seconds to daily buckets in the user's timezone.
            spans = split_by_local_midnight(
                prev_heartbeat, now, session.user_timezone
            )
            cross_midnight = len(spans) > 1
            for span in spans:
                await bundle.usage.add_seconds(
                    tenant_id=tenant_id,
                    user_id=session.user_id,
                    local_date=span.local_date,
                    seconds=span.seconds,
                    now=now,
                )

            # Re-evaluate the *pinned* policy document with fresh counters.
            today = local_date_for(now, session.user_timezone)
            daily_used = await bundle.usage.get_daily(
                tenant_id=tenant_id, user_id=session.user_id, local_date=today
            )
            doc = parse_policy(session.policy_document)
            ctx = EvaluationContext(
                user_age=session.user_age,
                user_timezone=session.user_timezone,
                now_utc=now,
                daily_used_seconds=int(daily_used),
                session_used_seconds=int(session.accumulated_active_seconds),
                approvals=set(session.approvals),
            )
            policy_result = evaluate(doc, ctx)

            if not policy_result.allowed:
                # Hard stop: end the session deterministically.  The heartbeat
                # above already advanced the counters to ``now``, so no extra
                # seconds need to be attributed.
                session.apply_end(now)
                await bundle.sessions.update(session)
                events.append(
                    DomainEvent(
                        event_type=EventType.SESSION_DENIED,
                        aggregate_id=session.id,
                        tenant_id=tenant_id,
                        occurred_at=now,
                        payload={"reason": policy_result.reason, "rule_id": policy_result.rule_id},
                    )
                )
                events.append(
                    DomainEvent(
                        event_type=EventType.SESSION_ENDED,
                        aggregate_id=session.id,
                        tenant_id=tenant_id,
                        occurred_at=now,
                        payload={"reason": policy_result.reason},
                    )
                )
                resp = {
                    "session_id": session.id,
                    "state": SessionState.ENDED.value,
                    "allowed": False,
                    "reason": policy_result.reason,
                    "rule_id": policy_result.rule_id,
                    "trace": [t.model_dump(mode="json") for t in policy_result.trace],
                    "accumulated_active_seconds": round(session.accumulated_active_seconds, 3),
                }
            else:
                await bundle.sessions.update(session)
                events.append(
                    DomainEvent(
                        event_type=EventType.SESSION_HEARTBEAT,
                        aggregate_id=session.id,
                        tenant_id=tenant_id,
                        occurred_at=now,
                        payload={
                            "sequence": sequence,
                            "elapsed_seconds": round(decision.elapsed_seconds, 3),
                            "cross_midnight": cross_midnight,
                        },
                    )
                )
                if cross_midnight:
                    events.append(
                        DomainEvent(
                            event_type=EventType.QUOTA_HIT,
                            aggregate_id=session.id,
                            tenant_id=tenant_id,
                            occurred_at=now,
                            payload={"reason": ReasonCode.CROSS_MIDNIGHT_DAILY_RESET.value},
                        )
                    )
                resp = {
                    "session_id": session.id,
                    "state": session.state.value,
                    "reason": ReasonCode.HEARTBEAT_ACCEPTED.value
                    if not cross_midnight
                    else ReasonCode.CROSS_MIDNIGHT_DAILY_RESET.value,
                    "accumulated_active_seconds": round(session.accumulated_active_seconds, 3),
                    "daily_used_seconds": int(daily_used),
                }

            await bundle.idempotency.store(
                tenant_id=tenant_id,
                idempotency_key=idem,
                request_type="session.heartbeat",
                request_hash=str(sequence),
                response=resp,
                now=now,
            )
            for ev in events:
                await bundle.outbox.add(ev)
            return {"ok": policy_result.allowed, "response": resp, "replayed": False}

        result = await self._uow.run(_tx)
        if result.get("replayed"):
            return ServiceResponse(
                ok=result["replay"].get("allowed", result["replay"].get("state") == "ACTIVE"),
                reason=ReasonCode.IDEMPOTENT_REPLAY,
                data=result["replay"],
            )
        if "error" in result:
            return ServiceResponse(ok=False, reason=result["error"])
        return ServiceResponse(
            ok=result["ok"],
            reason=ReasonCode(result["response"].get("reason", "OK")),
            data=result["response"],
        )

    # ----------------------------------------------------------------- pause
    async def pause(
        self,
        *,
        tenant_id: str,
        session_id: str,
        idempotency_key: str | None = None,
    ) -> ServiceResponse:
        return await self._simple_transition(
            tenant_id=tenant_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request_type="session.pause",
            apply=lambda s, now: s.apply_pause(now),
            event_type=EventType.SESSION_PAUSED,
            success_reason=ReasonCode.SESSION_PAUSED,
        )

    async def resume(
        self,
        *,
        tenant_id: str,
        session_id: str,
        idempotency_key: str | None = None,
    ) -> ServiceResponse:
        return await self._simple_transition(
            tenant_id=tenant_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request_type="session.resume",
            apply=lambda s, now: s.apply_resume(now),
            event_type=EventType.SESSION_RESUMED,
            success_reason=ReasonCode.SESSION_RESUMED,
        )

    async def end(
        self,
        *,
        tenant_id: str,
        session_id: str,
        idempotency_key: str | None = None,
    ) -> ServiceResponse:
        return await self._simple_transition(
            tenant_id=tenant_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request_type="session.end",
            apply=lambda s, now: s.apply_end(now),
            event_type=EventType.SESSION_ENDED,
            success_reason=ReasonCode.SESSION_ENDED,
            attribute_elapsed_to_daily=True,
        )

    async def _simple_transition(
        self,
        *,
        tenant_id: str,
        session_id: str,
        idempotency_key: str | None,
        request_type: str,
        apply,
        event_type: EventType,
        success_reason: ReasonCode,
        attribute_elapsed_to_daily: bool = False,
    ) -> ServiceResponse:
        idem = idempotency_key or f"{request_type}:{tenant_id}:{session_id}"

        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            existing = await bundle.idempotency.get(tenant_id=tenant_id, idempotency_key=idem)
            if existing and existing.get("request_type") == request_type:
                return {"replay": existing["response"], "replayed": True}

            session = await bundle.sessions.get(tenant_id=tenant_id, session_id=session_id)
            if session is None:
                return {"error": ReasonCode.SESSION_NOT_FOUND}
            if session.tenant_id != tenant_id:
                return {"error": ReasonCode.TENANT_ISOLATION_VIOLATION}

            now = self._clock.now()
            prev_heartbeat = session.last_heartbeat_at
            decision = apply(session, now)
            if not decision.accepted:
                # Idempotent "already in target state" is still ok=True.
                soft = decision.reason in (
                    ReasonCode.SESSION_ALREADY_ENDED,
                    ReasonCode.SESSION_NOT_ACTIVE,
                    ReasonCode.SESSION_NOT_PAUSED,
                )
                resp = {
                    "session_id": session.id,
                    "state": session.state.value,
                    "reason": decision.reason.value,
                }
                await bundle.idempotency.store(
                    tenant_id=tenant_id,
                    idempotency_key=idem,
                    request_type=request_type,
                    request_hash="",
                    response=resp,
                    now=now,
                )
                return {"ok": soft, "response": resp, "replayed": False, "soft": soft}

            if attribute_elapsed_to_daily and decision.elapsed_seconds > 0:
                for span in split_by_local_midnight(
                    prev_heartbeat, now, session.user_timezone
                ):
                    await bundle.usage.add_seconds(
                        tenant_id=tenant_id,
                        user_id=session.user_id,
                        local_date=span.local_date,
                        seconds=span.seconds,
                        now=now,
                    )

            await bundle.sessions.update(session)
            await bundle.outbox.add(
                DomainEvent(
                    event_type=event_type,
                    aggregate_id=session.id,
                    tenant_id=tenant_id,
                    occurred_at=now,
                    payload={
                        "elapsed_seconds": round(decision.elapsed_seconds, 3),
                    },
                )
            )
            resp = {
                "session_id": session.id,
                "state": session.state.value,
                "reason": success_reason.value,
            }
            await bundle.idempotency.store(
                tenant_id=tenant_id,
                idempotency_key=idem,
                request_type=request_type,
                request_hash="",
                response=resp,
                now=now,
            )
            return {"ok": True, "response": resp, "replayed": False}

        result = await self._uow.run(_tx)
        if result.get("replayed"):
            return ServiceResponse(
                ok=True, reason=ReasonCode.IDEMPOTENT_REPLAY, data=result["replay"]
            )
        if "error" in result:
            return ServiceResponse(ok=False, reason=result["error"])
        return ServiceResponse(
            ok=result["ok"], reason=ReasonCode(result["response"]["reason"]), data=result["response"]
        )

    # ----------------------------------------------------------------- usage
    async def query_usage(
        self,
        *,
        tenant_id: str,
        user_id: str,
        user_timezone: str,
    ) -> ServiceResponse:
        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            now = self._clock.now()
            today = local_date_for(now, user_timezone)
            daily = await bundle.usage.get_daily(
                tenant_id=tenant_id, user_id=user_id, local_date=today
            )
            active = await bundle.sessions.get_active_for_user(
                tenant_id=tenant_id, user_id=user_id
            )
            session_seconds = 0.0
            session_id = None
            state = None
            policy_version = None
            if active is not None:
                session_id = active.id
                state = active.state.value
                policy_version = active.policy_version
                # If active and the clock has advanced since last heartbeat we
                # include the in-flight elapsed for visibility.
                if active.state is SessionState.ACTIVE:
                    session_seconds = active.accumulated_active_seconds + max(
                        0.0, (now - active.last_heartbeat_at).total_seconds()
                    )
                else:
                    session_seconds = active.accumulated_active_seconds
            return {
                "user_id": user_id,
                "local_date": today.isoformat(),
                "daily_used_seconds": round(daily, 3),
                "current_session_id": session_id,
                "current_session_state": state,
                "current_session_seconds": round(session_seconds, 3),
                "current_policy_version": policy_version,
            }

        data = await self._uow.run(_tx)
        return ServiceResponse(ok=True, reason=ReasonCode.USAGE_QUERIED, data=data)


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "unique" in msg or "duplicate" in msg or "constraint" in msg
