from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SessionSvc, TenantId, IdempotencyKey
from app.domain.enums import ReasonCode, REASON_CODE_MESSAGES
from app.schemas.session import (
    SessionStartRequest,
    SessionHeartbeatRequest,
    SessionPauseRequest,
    SessionResumeRequest,
    SessionEndRequest,
    SessionResponse,
    SessionActionResponse,
    UsageQueryResponse,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _error(rc: ReasonCode, status: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"reason_code": rc.value, "message": REASON_CODE_MESSAGES.get(rc, "")},
    )


@router.post("", response_model=SessionActionResponse, status_code=201)
async def start_session(
    tenant_id: TenantId,
    svc: SessionSvc,
    body: SessionStartRequest,
    idempotency_key: IdempotencyKey = None,
):
    result, rc, msg = await svc.start_session(
        tenant_id=tenant_id,
        user_id=body.user_id,
        user_age=body.user_age,
        user_timezone=body.user_timezone,
        policy_version=body.policy_version,
        idempotency_key=idempotency_key,
    )
    if result is None:
        raise _error(rc, 404 if rc == ReasonCode.POLICY_NOT_FOUND else 400)
    if rc not in (ReasonCode.OK, ReasonCode.SESSION_ALREADY_ACTIVE, ReasonCode.CONCURRENT_SESSION_BLOCKED):
        raise HTTPException(
            status_code=403,
            detail={
                "reason_code": rc.value,
                "message": msg,
                "allowed": result.get("allowed", False),
                "trace": result.get("trace"),
            },
        )
    return SessionActionResponse(
        session=SessionResponse(**result),
        action="start",
        success=rc == ReasonCode.OK,
        reason_code=rc.value,
        message=msg,
        trace=result.get("trace") if isinstance(result.get("trace"), list) else None,
    )


@router.post("/{session_id}/heartbeat", response_model=SessionActionResponse)
async def heartbeat(
    tenant_id: TenantId,
    session_id: str,
    svc: SessionSvc,
    body: SessionHeartbeatRequest,
):
    result, rc, msg = await svc.heartbeat(tenant_id, session_id, body.sequence)
    if result is None:
        raise _error(rc, 404)
    if rc not in (ReasonCode.OK, ReasonCode.DUPLICATE_HEARTBEAT):
        raise _error(rc, 409 if rc == ReasonCode.HEARTBEAT_OUT_OF_ORDER else 400)
    trace_data = result.get("last_evaluation_detail", {}).get("trace") if result.get("last_evaluation_detail") else None
    return SessionActionResponse(
        session=SessionResponse(**result),
        action="heartbeat",
        success=rc == ReasonCode.OK,
        reason_code=rc.value,
        message=msg,
        trace=[trace_data] if trace_data else None,
    )


@router.post("/{session_id}/pause", response_model=SessionActionResponse)
async def pause_session(
    tenant_id: TenantId,
    session_id: str,
    svc: SessionSvc,
    body: SessionPauseRequest,
):
    result, rc, msg = await svc.pause(tenant_id, session_id, body.reason)
    if result is None:
        raise _error(rc, 404)
    if rc != ReasonCode.OK:
        raise _error(rc, 409)
    return SessionActionResponse(
        session=SessionResponse(**result),
        action="pause",
        success=True,
        reason_code=rc.value,
        message=msg,
    )


@router.post("/{session_id}/resume", response_model=SessionActionResponse)
async def resume_session(
    tenant_id: TenantId,
    session_id: str,
    svc: SessionSvc,
    body: SessionResumeRequest,
):
    result, rc, msg = await svc.resume(tenant_id, session_id)
    if result is None:
        raise _error(rc, 404)
    if rc != ReasonCode.OK:
        raise _error(rc, 409)
    return SessionActionResponse(
        session=SessionResponse(**result),
        action="resume",
        success=True,
        reason_code=rc.value,
        message=msg,
    )


@router.post("/{session_id}/end", response_model=SessionActionResponse)
async def end_session(
    tenant_id: TenantId,
    session_id: str,
    svc: SessionSvc,
    body: SessionEndRequest,
):
    result, rc, msg = await svc.end_session(tenant_id, session_id, body.reason)
    if result is None:
        raise _error(rc, 404)
    if rc != ReasonCode.OK:
        raise _error(rc, 409)
    return SessionActionResponse(
        session=SessionResponse(**result),
        action="end",
        success=True,
        reason_code=rc.value,
        message=msg,
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(tenant_id: TenantId, session_id: str, svc: SessionSvc):
    result = await svc.get_session(tenant_id, session_id)
    if result is None:
        raise _error(ReasonCode.SESSION_NOT_FOUND, 404)
    return SessionResponse(**result)


@router.get("/{session_id}/replay")
async def replay_session(tenant_id: TenantId, session_id: str, svc: SessionSvc):
    result = await svc.replay_session(tenant_id, session_id)
    if result is None:
        raise _error(ReasonCode.SESSION_NOT_FOUND, 404)
    return {"session_id": session_id, "events": result}


@router.get("/usage/{user_id}", response_model=UsageQueryResponse)
async def query_usage(
    tenant_id: TenantId,
    user_id: str,
    svc: SessionSvc,
    timezone: str = Query(default="UTC", alias="timezone"),
    date: str | None = Query(default=None, alias="date"),
):
    target_dt = None
    if date:
        try:
            target_dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use ISO-8601")
    result = await svc.query_usage(tenant_id, user_id, timezone, target_dt)
    return UsageQueryResponse(**result)
