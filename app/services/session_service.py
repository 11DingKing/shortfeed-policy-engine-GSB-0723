from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ReasonCode, SessionAction, SessionStatus, REASON_CODE_MESSAGES
from app.domain.policy.ast import PolicyAST
from app.domain.policy.engine import EvaluationContext, evaluate_policy
from app.domain.session.aggregate import SessionAggregate
from app.infrastructure.clock import Clock, SystemClock
from app.infrastructure.db.models.session import SessionDB
from app.infrastructure.db.models.session_event import SessionEvent
from app.infrastructure.id_generator import IdGenerator, Uuid4Generator
from app.infrastructure.repositories.policy_repo import PolicyRepository
from app.infrastructure.repositories.session_repo import SessionRepository
from app.infrastructure.repositories.outbox_repo import OutboxRepository


class SessionService:
    def __init__(
        self,
        db: AsyncSession,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._db = db
        self._clock = clock or SystemClock()
        self._id_gen = id_generator or Uuid4Generator()
        self._sessions = SessionRepository(db)
        self._policies = PolicyRepository(db)
        self._outbox = OutboxRepository(db)

    def _now(self) -> datetime:
        return self._clock.now()

    async def start_session(
        self,
        tenant_id: str,
        user_id: str,
        user_age: int | None = None,
        user_timezone: str = "UTC",
        policy_version: int | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, ReasonCode, str]:
        if idempotency_key:
            existing = await self._sessions.get_by_idempotency_key(tenant_id, idempotency_key)
            if existing:
                return self._session_to_response(existing), ReasonCode.OK, REASON_CODE_MESSAGES[ReasonCode.OK]

        if policy_version is not None:
            policy = await self._policies.get_by_version(tenant_id, policy_version)
        else:
            policy = await self._policies.get_latest(tenant_id)
        if policy is None:
            return None, ReasonCode.POLICY_NOT_FOUND, REASON_CODE_MESSAGES[ReasonCode.POLICY_NOT_FOUND]

        active = await self._sessions.get_active_by_user(tenant_id, user_id)
        if active:
            return self._session_to_response(active), ReasonCode.SESSION_ALREADY_ACTIVE, \
                REASON_CODE_MESSAGES[ReasonCode.SESSION_ALREADY_ACTIVE]

        now = self._now()
        ast = PolicyAST.model_validate(policy.ast_json)

        ctx = EvaluationContext(
            user_id=user_id,
            user_age=user_age,
            user_timezone=user_timezone,
            current_time=now,
            daily_used_minutes=await self._calc_daily_used_minutes(tenant_id, user_id, user_timezone),
            session_active_seconds=0,
        )
        result = evaluate_policy(ast, ctx)

        if not result.allowed:
            return {
                "allowed": False,
                "reason_code": result.reason_code.value,
                "message": REASON_CODE_MESSAGES.get(result.reason_code, str(result.reason_code)),
                "trace": result.trace.to_dict(),
            }, result.reason_code, REASON_CODE_MESSAGES.get(result.reason_code, "")

        session_id = self._id_gen.new_id()

        try:
            db_session = SessionDB(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                policy_id=policy.id,
                policy_version=policy.version,
                status=SessionStatus.ACTIVE.value,
                started_at=now,
                last_heartbeat_at=now,
                last_heartbeat_seq=0,
                total_active_seconds=0,
                user_timezone=user_timezone,
                user_age=user_age,
                last_evaluation_reason=ReasonCode.OK.value,
                last_evaluation_detail={"trace": result.trace.to_dict()},
                idempotency_key=idempotency_key,
            )
            await self._sessions.create(db_session)
        except sa_exc.IntegrityError:
            await self._db.rollback()
            active = await self._sessions.get_active_by_user(tenant_id, user_id)
            if active:
                return self._session_to_response(active), ReasonCode.CONCURRENT_SESSION_BLOCKED, \
                    REASON_CODE_MESSAGES[ReasonCode.CONCURRENT_SESSION_BLOCKED]
            raise

        await self._persist_events(db_session, SessionAggregate.create(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id,
            policy_id=policy.id, policy_version=policy.version, now=now,
            user_age=user_age, user_timezone=user_timezone, idempotency_key=idempotency_key,
        ))

        await self._write_outbox(session_id, tenant_id, "session.started", {
            "session_id": session_id, "user_id": user_id, "policy_version": policy.version,
        })

        resp = self._session_to_response(db_session)
        resp["allowed"] = True
        resp["reason_code"] = ReasonCode.OK.value
        resp["message"] = REASON_CODE_MESSAGES[ReasonCode.OK]
        resp["trace"] = result.trace.to_dict()
        return resp, ReasonCode.OK, REASON_CODE_MESSAGES[ReasonCode.OK]

    async def heartbeat(
        self,
        tenant_id: str,
        session_id: str,
        sequence: int,
    ) -> tuple[dict[str, Any] | None, ReasonCode, str]:
        db_session = await self._sessions.get_by_id(session_id, tenant_id)
        if db_session is None:
            return None, ReasonCode.SESSION_NOT_FOUND, REASON_CODE_MESSAGES[ReasonCode.SESSION_NOT_FOUND]

        if db_session.tenant_id != tenant_id:
            return None, ReasonCode.TENANT_MISMATCH, REASON_CODE_MESSAGES[ReasonCode.TENANT_MISMATCH]

        if db_session.status in (SessionStatus.ENDED.value, SessionStatus.EXPIRED.value):
            return self._session_to_response(db_session), ReasonCode.SESSION_ALREADY_ENDED, \
                REASON_CODE_MESSAGES[ReasonCode.SESSION_ALREADY_ENDED]

        if sequence < db_session.last_heartbeat_seq:
            return self._session_to_response(db_session), ReasonCode.HEARTBEAT_OUT_OF_ORDER, \
                REASON_CODE_MESSAGES[ReasonCode.HEARTBEAT_OUT_OF_ORDER]

        if sequence == db_session.last_heartbeat_seq:
            return self._session_to_response(db_session), ReasonCode.DUPLICATE_HEARTBEAT, \
                REASON_CODE_MESSAGES[ReasonCode.DUPLICATE_HEARTBEAT]

        if db_session.status == SessionStatus.PAUSED.value:
            return self._session_to_response(db_session), ReasonCode.SESSION_NOT_ACTIVE, \
                REASON_CODE_MESSAGES[ReasonCode.SESSION_NOT_ACTIVE]

        now = self._now()
        prev_hb = db_session.last_heartbeat_at or db_session.started_at
        delta = int((now - prev_hb).total_seconds())
        if delta > 0:
            db_session.total_active_seconds += delta
        db_session.last_heartbeat_at = now
        db_session.last_heartbeat_seq = sequence

        await self._re_evaluate(db_session, now)

        aggregate = SessionAggregate.from_db_row(db_session)
        ok, reason = aggregate.heartbeat(sequence, now)
        await self._persist_events(db_session, aggregate)
        await self._sessions.update(db_session)

        resp = self._session_to_response(db_session)
        resp["reason_code"] = reason.value
        resp["message"] = REASON_CODE_MESSAGES.get(reason, "")
        return resp, reason, REASON_CODE_MESSAGES.get(reason, "")

    async def pause(
        self, tenant_id: str, session_id: str, reason: str | None = None
    ) -> tuple[dict[str, Any] | None, ReasonCode, str]:
        db_session = await self._sessions.get_by_id(session_id, tenant_id)
        if db_session is None:
            return None, ReasonCode.SESSION_NOT_FOUND, REASON_CODE_MESSAGES[ReasonCode.SESSION_NOT_FOUND]
        now = self._now()
        aggregate = SessionAggregate.from_db_row(db_session)
        ok, rc = aggregate.pause(now)
        if not ok:
            return self._session_to_response(db_session), rc, REASON_CODE_MESSAGES[rc]
        self._apply_aggregate_to_db(aggregate, db_session)
        await self._persist_events(db_session, aggregate)
        await self._sessions.update(db_session)
        resp = self._session_to_response(db_session)
        resp["reason_code"] = rc.value
        resp["message"] = REASON_CODE_MESSAGES[rc]
        return resp, rc, REASON_CODE_MESSAGES[rc]

    async def resume(
        self, tenant_id: str, session_id: str
    ) -> tuple[dict[str, Any] | None, ReasonCode, str]:
        db_session = await self._sessions.get_by_id(session_id, tenant_id)
        if db_session is None:
            return None, ReasonCode.SESSION_NOT_FOUND, REASON_CODE_MESSAGES[ReasonCode.SESSION_NOT_FOUND]
        now = self._now()
        aggregate = SessionAggregate.from_db_row(db_session)
        ok, rc = aggregate.resume(now)
        if not ok:
            return self._session_to_response(db_session), rc, REASON_CODE_MESSAGES[rc]
        self._apply_aggregate_to_db(aggregate, db_session)
        await self._re_evaluate(db_session, now)
        await self._persist_events(db_session, aggregate)
        await self._sessions.update(db_session)
        resp = self._session_to_response(db_session)
        resp["reason_code"] = rc.value
        resp["message"] = REASON_CODE_MESSAGES[rc]
        return resp, rc, REASON_CODE_MESSAGES[rc]

    async def end_session(
        self, tenant_id: str, session_id: str, reason: str | None = None
    ) -> tuple[dict[str, Any] | None, ReasonCode, str]:
        db_session = await self._sessions.get_by_id(session_id, tenant_id)
        if db_session is None:
            return None, ReasonCode.SESSION_NOT_FOUND, REASON_CODE_MESSAGES[ReasonCode.SESSION_NOT_FOUND]
        now = self._now()
        aggregate = SessionAggregate.from_db_row(db_session)
        ok, rc = aggregate.end(now, reason)
        if not ok:
            return self._session_to_response(db_session), rc, REASON_CODE_MESSAGES[rc]
        self._apply_aggregate_to_db(aggregate, db_session)
        await self._persist_events(db_session, aggregate)
        await self._write_outbox(session_id, tenant_id, "session.ended", {
            "session_id": session_id, "user_id": db_session.user_id,
            "total_active_seconds": db_session.total_active_seconds,
        })
        await self._sessions.update(db_session)
        resp = self._session_to_response(db_session)
        resp["reason_code"] = rc.value
        resp["message"] = REASON_CODE_MESSAGES[rc]
        return resp, rc, REASON_CODE_MESSAGES[rc]

    async def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        db_session = await self._sessions.get_by_id(session_id, tenant_id)
        if db_session is None:
            return None
        return self._session_to_response(db_session)

    async def query_usage(
        self, tenant_id: str, user_id: str, user_timezone: str = "UTC", target_date: datetime | None = None
    ) -> dict[str, Any]:
        tz = pytz.timezone(user_timezone)
        if target_date is None:
            target_date = self._clock.now().astimezone(tz).date()
        else:
            target_date = target_date.astimezone(tz).date()
        total_sec = await self._sessions.get_total_active_seconds_for_date(
            tenant_id, user_id, target_date, user_timezone
        )
        count = await self._sessions.count_sessions_for_date(tenant_id, user_id, target_date, user_timezone)
        active = await self._sessions.get_active_by_user(tenant_id, user_id)
        daily_limit = await self._get_daily_limit(tenant_id, user_timezone)
        remaining = None
        if daily_limit is not None:
            remaining = max(0, daily_limit - (total_sec / 60.0))
        return {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "date": target_date.isoformat(),
            "total_sessions": count,
            "total_active_seconds": total_sec,
            "total_active_minutes": round(total_sec / 60.0, 2),
            "daily_limit_minutes": daily_limit,
            "remaining_minutes": round(remaining, 2) if remaining is not None else None,
            "current_session_id": active.id if active else None,
            "current_session_status": active.status if active else None,
        }

    async def replay_session(self, tenant_id: str, session_id: str) -> list[dict[str, Any]] | None:
        db_session = await self._sessions.get_by_id(session_id, tenant_id)
        if db_session is None:
            return None
        events = await self._sessions.get_events(session_id)
        policy = await self._policies.get_by_id(db_session.policy_id)
        if policy is None:
            return None
        ast = PolicyAST.model_validate(policy.ast_json)
        replay: list[dict[str, Any]] = []
        active_sec = 0
        for evt in events:
            ctx = EvaluationContext(
                user_id=db_session.user_id,
                user_age=db_session.user_age,
                user_timezone=db_session.user_timezone,
                current_time=evt.occurred_at,
                daily_used_minutes=0,
                session_active_seconds=active_sec,
            )
            result = evaluate_policy(ast, ctx)
            replay.append({
                "sequence": evt.sequence,
                "action": evt.action,
                "occurred_at": evt.occurred_at.isoformat(),
                "reason_code": evt.reason_code,
                "evaluation_allowed": result.allowed,
                "evaluation_reason": result.reason_code.value,
                "trace": result.trace.to_dict(),
            })
            if evt.action == SessionAction.HEARTBEAT.value:
                detail = evt.detail or {}
                active_sec += detail.get("delta_seconds", 0)
        return replay

    async def _calc_daily_used_minutes(self, tenant_id: str, user_id: str, user_timezone: str) -> int:
        tz = pytz.timezone(user_timezone)
        today = self._clock.now().astimezone(tz).date()
        total_sec = await self._sessions.get_total_active_seconds_for_date(
            tenant_id, user_id, today, user_timezone
        )
        return total_sec // 60

    async def _get_daily_limit(self, tenant_id: str, user_timezone: str) -> int | None:
        policy = await self._policies.get_latest(tenant_id)
        if policy is None:
            return None
        ast = PolicyAST.model_validate(policy.ast_json)
        return _extract_daily_limit(ast.rule)

    async def _re_evaluate(self, db_session: SessionDB, now: datetime) -> None:
        policy = await self._policies.get_by_id(db_session.policy_id)
        if policy is None:
            return
        ast = PolicyAST.model_validate(policy.ast_json)
        ctx = EvaluationContext(
            user_id=db_session.user_id,
            user_age=db_session.user_age,
            user_timezone=db_session.user_timezone,
            current_time=now,
            daily_used_minutes=db_session.total_active_seconds // 60,
            session_active_seconds=db_session.total_active_seconds,
        )
        result = evaluate_policy(ast, ctx)
        db_session.last_evaluation_reason = result.reason_code.value
        db_session.last_evaluation_detail = {"trace": result.trace.to_dict()}
        if not result.allowed:
            aggregate = SessionAggregate.from_db_row(db_session)
            aggregate.apply_evaluation_result(False, result.reason_code, result.trace.to_dict(), now)
            self._apply_aggregate_to_db(aggregate, db_session)
            await self._persist_events(db_session, aggregate)
            await self._write_outbox(db_session.id, db_session.tenant_id, "session.expired", {
                "session_id": db_session.id, "reason_code": result.reason_code.value,
            })

    async def _persist_events(self, db_session: SessionDB, aggregate: SessionAggregate) -> None:
        events = aggregate.drain_events()
        for i, evt in enumerate(events):
            seq = await self._next_event_seq(db_session.id)
            await self._sessions.add_event(SessionEvent(
                id=self._id_gen.new_id(),
                session_id=evt.session_id,
                tenant_id=evt.tenant_id,
                action=evt.action.value,
                sequence=seq + i,
                reason_code=evt.reason_code.value,
                detail=evt.detail,
                occurred_at=evt.occurred_at,
            ))

    async def _next_event_seq(self, session_id: str) -> int:
        events = await self._sessions.get_events(session_id)
        return len(events) + 1

    async def _write_outbox(self, aggregate_id: str, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
        await self._outbox.save(
            message_id=self._id_gen.new_id(),
            aggregate_type="session",
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload={"tenant_id": tenant_id, **payload},
        )

    @staticmethod
    def _apply_aggregate_to_db(aggregate: SessionAggregate, db_session: SessionDB) -> None:
        db_session.status = aggregate.status.value
        db_session.ended_at = aggregate.ended_at
        db_session.last_heartbeat_at = aggregate.last_heartbeat_at
        db_session.last_heartbeat_seq = aggregate.last_heartbeat_seq
        db_session.paused_at = aggregate.paused_at
        db_session.total_active_seconds = aggregate.total_active_seconds
        db_session.last_evaluation_reason = aggregate.last_evaluation_reason
        db_session.last_evaluation_detail = aggregate.last_evaluation_detail

    @staticmethod
    def _session_to_response(db_session: SessionDB) -> dict[str, Any]:
        return {
            "id": db_session.id,
            "tenant_id": db_session.tenant_id,
            "user_id": db_session.user_id,
            "policy_id": db_session.policy_id,
            "policy_version": db_session.policy_version,
            "status": db_session.status,
            "started_at": db_session.started_at,
            "ended_at": db_session.ended_at,
            "last_heartbeat_at": db_session.last_heartbeat_at,
            "total_active_seconds": db_session.total_active_seconds,
            "total_active_minutes": round(db_session.total_active_seconds / 60.0, 2),
            "last_evaluation_reason": db_session.last_evaluation_reason,
            "last_evaluation_detail": db_session.last_evaluation_detail,
        }


def _extract_daily_limit(rule: Any) -> int | None:
    type_name = getattr(rule, "type", None)
    if type_name == "daily_limit":
        return rule.max_minutes_per_day
    if type_name == "and":
        for r in rule.rules:
            result = _extract_daily_limit(r)
            if result is not None:
                return result
    if type_name == "or":
        for r in rule.rules:
            result = _extract_daily_limit(r)
            if result is not None:
                return result
    if type_name == "not":
        return None
    return None
