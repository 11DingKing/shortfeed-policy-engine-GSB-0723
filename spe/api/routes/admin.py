"""Admin endpoints: health, reason-code catalog, historical replay."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...container import AppContainer
from ...domain.reason_codes import REASON_CODE_DESCRIPTIONS, ReasonCode
from ..deps import get_container, require_tenant
from ..web import respond

router = APIRouter(tags=["admin"])


@router.get("/healthz")
async def healthz(container: AppContainer = Depends(get_container)):
    return {"ok": True, "service": "shortfeed-policy-engine"}


@router.get("/reason-codes")
async def reason_codes():
    return {
        "ok": True,
        "reason": ReasonCode.OK.value,
        "data": {
            code.value: desc for code, desc in sorted(REASON_CODE_DESCRIPTIONS.items(), key=lambda kv: kv[0].value)
        },
    }


@router.post("/sessions/{session_id}/replay")
async def replay(
    session_id: str,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.replays.replay(tenant_id=tenant, session_id=session_id)
    return respond(result)


@router.get("/")
async def root():
    return {
        "ok": True,
        "service": "shortfeed-policy-engine",
        "docs": "/docs",
        "reason_codes": "/api/v1/reason-codes",
        "health": "/api/v1/healthz",
    }
