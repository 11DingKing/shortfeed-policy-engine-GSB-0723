from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.outbox import OutboxMessage


class OutboxRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def save(
        self,
        message_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> OutboxMessage:
        msg = OutboxMessage(
            id=message_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            processed=False,
            retry_count=0,
        )
        self._db.add(msg)
        await self._db.flush()
        return msg

    async def get_unprocessed(self, limit: int = 100) -> list[OutboxMessage]:
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.processed.is_(False))
            .order_by(OutboxMessage.created_at.asc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def mark_processed(self, message_id: str, processed_at: datetime) -> None:
        stmt = (
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(processed=True, processed_at=processed_at)
        )
        await self._db.execute(stmt)

    async def mark_failed(self, message_id: str, error: str) -> None:
        stmt = (
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(error=error, retry_count=OutboxMessage.retry_count + 1)
        )
        await self._db.execute(stmt)
