from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any

import pytz
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SessionStatus
from app.infrastructure.db.models.session import SessionDB
from app.infrastructure.db.models.session_event import SessionEvent


def _day_bounds(target_date: date, user_timezone: str) -> tuple[datetime, datetime]:
    tz = pytz.timezone(user_timezone)
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, session: SessionDB) -> SessionDB:
        self._db.add(session)
        await self._db.flush()
        return session

    async def get_by_id(self, session_id: str, tenant_id: str) -> SessionDB | None:
        stmt = select(SessionDB).where(
            SessionDB.id == session_id,
            SessionDB.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_user(self, tenant_id: str, user_id: str) -> SessionDB | None:
        stmt = select(SessionDB).where(
            SessionDB.tenant_id == tenant_id,
            SessionDB.user_id == user_id,
            SessionDB.status.in_([SessionStatus.ACTIVE.value, SessionStatus.PAUSED.value]),
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> SessionDB | None:
        stmt = select(SessionDB).where(
            SessionDB.tenant_id == tenant_id,
            SessionDB.idempotency_key == idempotency_key,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, session: SessionDB) -> SessionDB:
        await self._db.flush()
        return session

    async def add_event(self, event: SessionEvent) -> None:
        self._db.add(event)
        await self._db.flush()

    async def get_events(self, session_id: str) -> list[SessionEvent]:
        stmt = (
            select(SessionEvent)
            .where(SessionEvent.session_id == session_id)
            .order_by(SessionEvent.sequence.asc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_total_active_seconds_for_date(
        self, tenant_id: str, user_id: str, target_date: date, user_timezone: str
    ) -> int:
        day_start, day_end = _day_bounds(target_date, user_timezone)
        stmt = select(func.coalesce(func.sum(SessionDB.total_active_seconds), 0)).where(
            SessionDB.tenant_id == tenant_id,
            SessionDB.user_id == user_id,
            SessionDB.started_at >= day_start,
            SessionDB.started_at < day_end,
        )
        result = await self._db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_sessions_for_date(
        self, tenant_id: str, user_id: str, target_date: date, user_timezone: str
    ) -> int:
        day_start, day_end = _day_bounds(target_date, user_timezone)
        stmt = select(func.count(SessionDB.id)).where(
            SessionDB.tenant_id == tenant_id,
            SessionDB.user_id == user_id,
            SessionDB.started_at >= day_start,
            SessionDB.started_at < day_end,
        )
        result = await self._db.execute(stmt)
        return int(result.scalar_one() or 0)
