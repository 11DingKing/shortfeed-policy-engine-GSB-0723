"""Async engine and session factory.

A single async engine is created per process from settings. Tests point the DSN
at an in-memory / temp SQLite database; production uses PostgreSQL via asyncpg.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


def make_engine(database_url: str, echo: bool = False) -> AsyncEngine:
    """Create an async engine for the given DSN."""
    connect_args: dict = {}
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            # Share one in-memory connection across all sessions so schema and
            # data persist for the lifetime of the engine (used by tests).
            kwargs["poolclass"] = StaticPool
    return create_async_engine(
        database_url, echo=echo, future=True, connect_args=connect_args, **kwargs
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


async def iter_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
