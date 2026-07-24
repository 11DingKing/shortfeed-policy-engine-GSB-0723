from __future__ import annotations

from typing import Annotated, AsyncGenerator

from fastapi import Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.session import get_db_session
from app.infrastructure.clock import Clock, SystemClock
from app.infrastructure.id_generator import IdGenerator, Uuid4Generator
from app.services.policy_service import PolicyService
from app.services.session_service import SessionService


def get_clock() -> Clock:
    return SystemClock()


def get_id_generator() -> IdGenerator:
    return Uuid4Generator()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


DBSession = Annotated[AsyncSession, Depends(get_db)]


def get_tenant_id(x_tenant_id: str = Header(..., alias="X-Tenant-Id")) -> str:
    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")
    return x_tenant_id.strip()


TenantId = Annotated[str, Depends(get_tenant_id)]


def get_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    return idempotency_key


IdempotencyKey = Annotated[str | None, Depends(get_idempotency_key)]


def get_policy_service(db: DBSession) -> PolicyService:
    return PolicyService(db=db)


def get_session_service(db: DBSession) -> SessionService:
    return SessionService(db=db)


PolicySvc = Annotated[PolicyService, Depends(get_policy_service)]
SessionSvc = Annotated[SessionService, Depends(get_session_service)]
