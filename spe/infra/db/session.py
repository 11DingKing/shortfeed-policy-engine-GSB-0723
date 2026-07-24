"""Async SQLAlchemy session factory and engine management."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .base import Base
from .models import (  # noqa: F401  (ensure all models are registered with Base.metadata)
    DailyUsageRow,
    IdempotencyRow,
    OutboxRow,
    PolicyRow,
    PolicyVersionRow,
    SessionRow,
)


class Database:
    """Owns the async engine and exposes a session factory.

    A single instance is created at app startup and shared across requests via
    FastAPI dependency injection.  In tests the factory is overridden to point
    at an in-memory or temporary database.
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._url = url
        self._engine: AsyncEngine = create_async_engine(url, echo=echo, future=True)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def connect(self) -> AsyncConnection:
        return await self._engine.connect()

    async def create_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
