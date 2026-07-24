from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.infrastructure.clock import FixedClock
from app.infrastructure.db.base import Base
from app.infrastructure.id_generator import SequentialGenerator
from app.infrastructure.db.models import *  # noqa: F401, F403

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/shortfeed_policy_test",
)


def _has_test_db() -> bool:
    db_url = os.environ.get("TEST_DATABASE_URL", "")
    if not db_url:
        return False
    try:
        import asyncpg  # noqa: F401
        return True
    except ImportError:
        return False


requires_postgres = pytest.mark.skipif(
    not _has_test_db(),
    reason="Requires PostgreSQL connection (set TEST_DATABASE_URL env var)",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with async_session() as session:
        from sqlalchemy import text
        for table in ["outbox_messages", "session_events", "daily_usage", "sessions", "policies", "idempotency_keys"]:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.execute(text("DELETE FROM tenants WHERE id != 'default'"))
        await session.commit()
        yield session


@pytest.fixture
def fixed_clock():
    return FixedClock(datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc))


@pytest.fixture
def seq_id_generator():
    return SequentialGenerator(prefix="test")
