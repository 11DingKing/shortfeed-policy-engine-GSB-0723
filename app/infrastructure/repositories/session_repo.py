from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any

import pytz
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SessionStatus
from app.infrastructure.db.models.session import SessionDB
from app.infrastructure.db.models.session_event import SessionEvent
from app.infrastructure.db.models.daily_usage import DailyUsage


def _day_bounds(target_date: date, user_timezone: str) -> tuple[datetime, datetime]:
    tz = pytz.timezone(user_timezone)
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


def split_seconds_by_local_day(
    start_utc: datetime,
    end_utc: datetime,
    user_timezone: str,
) -> dict[date, int]:
    tz = pytz.timezone(user_timezone)
    start_local = start_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)
    result: dict[date, int] = {}
    current = start_local
    while current < end_local:
        current_date = current.date()
        day_end_local = datetime.combine(current_date + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        segment_end = min(end_local, day_end_local)
        seconds = int((segment_end - current).total_seconds())
        if seconds > 0:
            result[current_date] = result.get(current_date, 0) + seconds
        current = segment_end
    return result


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

    async def get_by_id_for_update(self, session_id: str, tenant_id: str) -> SessionDB | None:
        stmt = (
            select(SessionDB)
            .where(
                SessionDB.id == session_id,
                SessionDB.tenant_id == tenant_id,
            )
            .with_for_update()
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

    async def add_daily_usage(
        self, tenant_id: str, user_id: str, target_date: date,
        user_timezone: str, seconds: int,
    ) -> None:
        stmt = (
            pg_insert(DailyUsage)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                usage_date=target_date,
                user_timezone=user_timezone,
                total_seconds=seconds,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "user_id", "usage_date", "user_timezone"],
                set_={"total_seconds": DailyUsage.total_seconds + seconds},
            )
        )
        await self._db.execute(stmt)

    async def get_daily_used_seconds(
        self, tenant_id: str, user_id: str, target_date: date, user_timezone: str,
    ) -> int:
        stmt = select(func.coalesce(DailyUsage.total_seconds, 0)).where(
            DailyUsage.tenant_id == tenant_id,
            DailyUsage.user_id == user_id,
            DailyUsage.usage_date == target_date,
            DailyUsage.user_timezone == user_timezone,
        )
        result = await self._db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_daily_usage_up_to(
        self, tenant_id: str, user_id: str, user_timezone: str, before_utc: datetime,
    ) -> dict[date, int]:
        tz = pytz.timezone(user_timezone)
        before_local = before_utc.astimezone(tz)
        stmt = select(DailyUsage).where(
            DailyUsage.tenant_id == tenant_id,
            DailyUsage.user_id == user_id,
            DailyUsage.user_timezone == user_timezone,
            DailyUsage.usage_date < before_local.date(),
        )
        result = await self._db.execute(stmt)
        return {row.usage_date: row.total_seconds for row in result.scalars().all()}

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
