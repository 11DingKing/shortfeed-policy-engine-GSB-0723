"""SQLAlchemy UnitOfWork / RepoBundle implementation."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...domain.repositories import RepoBundle
from .repositories.repositories import (
    IdempotencyRepository,
    OutboxRepository,
    PolicyRepository,
    SessionRepository,
    UsageRepository,
)


class SqlAlchemyBundle:
    """Concrete :class:`RepoBundle` sharing one :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.policies = PolicyRepository(session)
        self.sessions = SessionRepository(session)
        self.usage = UsageRepository(session)
        self.outbox = OutboxRepository(session)
        self.idempotency = IdempotencyRepository(session)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def run(self, fn: Callable[[RepoBundle], Awaitable[object]]):
        async with self._factory() as session:
            bundle = SqlAlchemyBundle(session)
            try:
                result = await fn(bundle)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
