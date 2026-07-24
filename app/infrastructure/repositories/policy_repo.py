from __future__ import annotations

from typing import Any
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.policy import Policy


class PolicyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        policy_id: str,
        tenant_id: str,
        version: int,
        name: str,
        description: str | None,
        ast_json: dict[str, Any],
        created_by: str | None,
    ) -> Policy:
        policy = Policy(
            id=policy_id,
            tenant_id=tenant_id,
            version=version,
            name=name,
            description=description,
            ast_json=ast_json,
            is_active=True,
            created_by=created_by,
        )
        self._db.add(policy)
        await self._db.flush()
        return policy

    async def get_by_version(self, tenant_id: str, version: int) -> Policy | None:
        stmt = select(Policy).where(
            Policy.tenant_id == tenant_id,
            Policy.version == version,
            Policy.is_active.is_(True),
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest(self, tenant_id: str) -> Policy | None:
        stmt = (
            select(Policy)
            .where(Policy.tenant_id == tenant_id, Policy.is_active.is_(True))
            .order_by(Policy.version.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, policy_id: str) -> Policy | None:
        stmt = select(Policy).where(Policy.id == policy_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def next_version(self, tenant_id: str) -> int:
        stmt = select(func.coalesce(func.max(Policy.version), 0)).where(
            Policy.tenant_id == tenant_id
        )
        result = await self._db.execute(stmt)
        return (result.scalar_one() or 0) + 1

    async def list_versions(self, tenant_id: str) -> list[Policy]:
        stmt = (
            select(Policy)
            .where(Policy.tenant_id == tenant_id)
            .order_by(Policy.version.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
