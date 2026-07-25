"""FastAPI dependencies.

Provides the per-request database session and request-scoped services, plus the
tenant resolver. The container is stored on ``app.state`` at startup so the clock
and id generator injected there flow through to every request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from spe.container import Container, Services


def get_container(request: Request) -> Container:
    return request.app.state.container


async def get_db(
    container: Annotated[Container, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session, committing on success."""
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_services(
    container: Annotated[Container, Depends(get_container)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Services:
    return container.services_for(db)


def get_tenant_id(
    x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")],
) -> str:
    """Resolve the caller's tenant from the required ``X-Tenant-ID`` header.

    Every route is tenant-scoped; there is no cross-tenant access path.
    """
    return x_tenant_id


TenantId = Annotated[str, Depends(get_tenant_id)]
Svc = Annotated[Services, Depends(get_services)]
Db = Annotated[AsyncSession, Depends(get_db)]
