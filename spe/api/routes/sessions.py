"""Session lifecycle endpoints: start, heartbeat, pause, resume, end, usage."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...container import AppContainer
from ..deps import get_container, require_tenant
from ..schemas.http import (
    HeartbeatRequest,
    StartSessionRequest,
    TransitionRequest,
)
from ..web import respond

router = APIRouter(tags=["sessions"])


@router.post("/sessions/start")
async def start_session(
    body: StartSessionRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.sessions.start(
        tenant_id=tenant,
        user_id=body.user_id,
        policy_id=body.policy_id,
        idempotency_key=body.idempotency_key,
        user_age=body.user_age,
        user_timezone=body.user_timezone,
        approvals=body.approvals,
        initial_sequence=body.initial_sequence,
    )
    return respond(result)


@router.post("/sessions/{session_id}/heartbeat")
async def heartbeat(
    session_id: str,
    body: HeartbeatRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.sessions.heartbeat(
        tenant_id=tenant,
        session_id=session_id,
        sequence=body.sequence,
        idempotency_key=body.idempotency_key,
    )
    return respond(result)


@router.post("/sessions/{session_id}/pause")
async def pause(
    session_id: str,
    body: TransitionRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.sessions.pause(
        tenant_id=tenant,
        session_id=session_id,
        idempotency_key=body.idempotency_key,
    )
    return respond(result)


@router.post("/sessions/{session_id}/resume")
async def resume(
    session_id: str,
    body: TransitionRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.sessions.resume(
        tenant_id=tenant,
        session_id=session_id,
        idempotency_key=body.idempotency_key,
    )
    return respond(result)


@router.post("/sessions/{session_id}/end")
async def end(
    session_id: str,
    body: TransitionRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.sessions.end(
        tenant_id=tenant,
        session_id=session_id,
        idempotency_key=body.idempotency_key,
    )
    return respond(result)


@router.get("/usage")
async def usage(
    user_id: str,
    user_timezone: str,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.sessions.query_usage(
        tenant_id=tenant,
        user_id=user_id,
        user_timezone=user_timezone,
    )
    return respond(result)
