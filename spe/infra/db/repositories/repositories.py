"""SQLAlchemy repository implementations.

All repositories take an :class:`AsyncSession` in their constructor, which is
managed by :class:`SqlAlchemyUnitOfWork`.  None of them commit — the unit of
work commits (or rolls back) atomically so that outbox events, mutations, and
idempotency records are published transactionally.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ....domain.events import DomainEvent
from ....domain.session import SessionAggregate
from ..models import (
    DailyUsageRow,
    IdempotencyRow,
    OutboxRow,
    PolicyRow,
    PolicyVersionRow,
    SessionRow,
)
from .mappers import apply_session_to_row, row_to_session


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        name: str,
        description: str,
        created_by: str | None,
        now: datetime,
    ) -> None:
        self._s.add(
            PolicyRow(
                id=policy_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
                created_at=now,
                created_by=created_by,
            )
        )

    async def exists(self, *, tenant_id: str, policy_id: str) -> bool:
        q = select(func.count()).select_from(PolicyRow).where(
            PolicyRow.tenant_id == tenant_id, PolicyRow.id == policy_id
        )
        res = await self._s.scalar(q)
        return bool(res)

    async def publish_version(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        document: dict,
        published_by: str | None,
        now: datetime,
    ) -> tuple[str, int]:
        # Compute next version number under row lock (scalar subquery).
        q = select(func.count()).select_from(PolicyVersionRow).where(
            PolicyVersionRow.tenant_id == tenant_id,
            PolicyVersionRow.policy_id == policy_id,
        )
        existing = await self._s.scalar(q)
        next_version = int(existing or 0) + 1
        version_id = f"{policy_id}:v{next_version}"

        # Mark all current versions for this (tenant, policy) as not current.
        await self._s.execute(
            PolicyVersionRow.__table__.update()  # type: ignore[attr-defined]
            .where(
                PolicyVersionRow.tenant_id == tenant_id,
                PolicyVersionRow.policy_id == policy_id,
                PolicyVersionRow.is_current.is_(True),
            )
            .values(is_current=False)
        )

        self._s.add(
            PolicyVersionRow(
                id=version_id,
                tenant_id=tenant_id,
                policy_id=policy_id,
                version=next_version,
                document=document,
                is_current=True,
                published_at=now,
                published_by=published_by,
            )
        )
        # Flush so the caller can see the new version in the same transaction.
        await self._s.flush()
        return version_id, next_version

    async def get_current(self, *, tenant_id: str, policy_id: str) -> PolicyVersionRow | None:
        q = select(PolicyVersionRow).where(
            PolicyVersionRow.tenant_id == tenant_id,
            PolicyVersionRow.policy_id == policy_id,
            PolicyVersionRow.is_current.is_(True),
        )
        return (await self._s.execute(q)).scalar_one_or_none()

    async def get_version(
        self, *, tenant_id: str, policy_id: str, version: int
    ) -> PolicyVersionRow | None:
        q = select(PolicyVersionRow).where(
            PolicyVersionRow.tenant_id == tenant_id,
            PolicyVersionRow.policy_id == policy_id,
            PolicyVersionRow.version == version,
        )
        return (await self._s.execute(q)).scalar_one_or_none()

    async def list_versions(self, *, tenant_id: str, policy_id: str) -> Sequence[PolicyVersionRow]:
        q = (
            select(PolicyVersionRow)
            .where(
                PolicyVersionRow.tenant_id == tenant_id,
                PolicyVersionRow.policy_id == policy_id,
            )
            .order_by(PolicyVersionRow.version.asc())
        )
        return list((await self._s.execute(q)).scalars())


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(self, session: SessionAggregate) -> None:
        row = SessionRow()
        apply_session_to_row(session, row)
        self._s.add(row)
        await self._s.flush()

    async def get(self, *, tenant_id: str, session_id: str) -> SessionAggregate | None:
        q = select(SessionRow).where(
            SessionRow.tenant_id == tenant_id, SessionRow.id == session_id
        )
        row = (await self._s.execute(q)).scalar_one_or_none()
        return row_to_session(row) if row else None

    async def get_active_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> SessionAggregate | None:
        q = select(SessionRow).where(
            SessionRow.tenant_id == tenant_id,
            SessionRow.user_id == user_id,
            SessionRow.state.in_(["ACTIVE", "PAUSED"]),
        )
        row = (await self._s.execute(q)).scalar_one_or_none()
        return row_to_session(row) if row else None

    async def update(self, session: SessionAggregate) -> None:
        q = select(SessionRow).where(
            SessionRow.tenant_id == session.tenant_id, SessionRow.id == session.id
        )
        row = (await self._s.execute(q)).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"session row vanished mid-txn: {session.id}")
        apply_session_to_row(session, row)
        await self._s.flush()


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_seconds(
        self,
        *,
        tenant_id: str,
        user_id: str,
        local_date: date,
        seconds: float,
        now: datetime,
    ) -> None:
        # Dialect-agnostic upsert: try insert, on conflict update.
        # We use the SQLAlchemy core insert with on_conflict for Postgres,
        # but for SQLite we use a SELECT then INSERT/UPDATE.
        dialect = self._s.bind.dialect.name if self._s.bind else "sqlite"
        if dialect == "postgresql":
            stmt = (
                pg_insert(DailyUsageRow.__table__)  # type: ignore[attr-defined]
                .values(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    local_date=local_date,
                    seconds=seconds,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "user_id", "local_date"],
                    set_={
                        "seconds": DailyUsageRow.__table__.c.seconds + seconds,  # type: ignore[attr-defined]
                        "updated_at": now,
                    },
                )
            )
            await self._s.execute(stmt)
        else:
            q = select(DailyUsageRow).where(
                DailyUsageRow.tenant_id == tenant_id,
                DailyUsageRow.user_id == user_id,
                DailyUsageRow.local_date == local_date,
            )
            row = (await self._s.execute(q)).scalar_one_or_none()
            if row is None:
                self._s.add(
                    DailyUsageRow(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        local_date=local_date,
                        seconds=seconds,
                        updated_at=now,
                    )
                )
            else:
                row.seconds = float(row.seconds) + seconds
                row.updated_at = now
            await self._s.flush()

    async def get_daily(self, *, tenant_id: str, user_id: str, local_date: date) -> float:
        q = select(DailyUsageRow.seconds).where(
            DailyUsageRow.tenant_id == tenant_id,
            DailyUsageRow.user_id == user_id,
            DailyUsageRow.local_date == local_date,
        )
        val = await self._s.scalar(q)
        return float(val or 0.0)


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, event: DomainEvent) -> None:
        self._s.add(_row_from_event(event))

    async def add_batch(self, events: Iterable[DomainEvent]) -> None:
        for ev in events:
            self._s.add(_row_from_event(ev))

    async def list_for_aggregate(
        self, *, tenant_id: str, aggregate_id: str
    ) -> Sequence[DomainEvent]:
        q = (
            select(OutboxRow)
            .where(
                OutboxRow.tenant_id == tenant_id,
                OutboxRow.aggregate_id == aggregate_id,
            )
            .order_by(OutboxRow.id.asc())
        )
        rows = list((await self._s.execute(q)).scalars())
        return [_event_from_row(r) for r in rows]


def _row_from_event(ev: DomainEvent) -> OutboxRow:

    return OutboxRow(
        tenant_id=ev.tenant_id,
        event_type=ev.event_type.value,
        aggregate_id=ev.aggregate_id,
        occurred_at=ev.occurred_at,
        payload=dict(ev.payload),
        idempotency_key=ev.idempotency_key,
        dispatched=False,
    )


def _event_from_row(row: OutboxRow) -> DomainEvent:
    from ....domain.events import EventType

    return DomainEvent(
        event_type=EventType(row.event_type),
        aggregate_id=row.aggregate_id,
        tenant_id=row.tenant_id,
        occurred_at=row.occurred_at,
        payload=dict(row.payload or {}),
        idempotency_key=row.idempotency_key,
    )


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, *, tenant_id: str, idempotency_key: str) -> dict | None:
        q = select(IdempotencyRow).where(
            IdempotencyRow.tenant_id == tenant_id,
            IdempotencyRow.idempotency_key == idempotency_key,
        )
        row = (await self._s.execute(q)).scalar_one_or_none()
        if row is None:
            return None
        return {
            "request_type": row.request_type,
            "request_hash": row.request_hash,
            "response": dict(row.response or {}),
            "created_at": row.created_at,
        }

    async def store(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        request_type: str,
        request_hash: str,
        response: dict,
        now: datetime,
    ) -> None:
        self._s.add(
            IdempotencyRow(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                request_type=request_type,
                request_hash=request_hash,
                response=response,
                created_at=now,
            )
        )
        await self._s.flush()
