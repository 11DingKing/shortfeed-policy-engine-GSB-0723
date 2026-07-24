"""FastAPI dependencies."""
from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from ..container import AppContainer


def get_container(request: Request) -> AppContainer:
    container: AppContainer | None = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="application not initialised")
    return container


def require_tenant(x_tenant_id: str = Header(..., alias="X-Tenant-Id")) -> str:
    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id header is required",
        )
    return x_tenant_id.strip()
