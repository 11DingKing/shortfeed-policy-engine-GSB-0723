from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.idempotency import IdempotencyKey


class IdempotencyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, tenant_id: str, key: str) -> IdempotencyKey | None:
        stmt = select(IdempotencyKey).where(
            IdempotencyKey.tenant_id == tenant_id,
            IdempotencyKey.key == key,
        )
        result = await self._db.execute(stmt)
        record = result.scalar_one_or_none()
        if record and record.expires_at < datetime.now(record.expires_at.tzinfo):
            return None
        return record

    async def save(
        self,
        record_id: str,
        tenant_id: str,
        key: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
        expires_at: datetime,
    ) -> IdempotencyKey:
        record = IdempotencyKey(
            id=record_id,
            tenant_id=tenant_id,
            key=key,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            expires_at=expires_at,
        )
        self._db.add(record)
        await self._db.flush()
        return record
