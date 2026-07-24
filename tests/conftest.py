from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.clock import FixedClock
from app.infrastructure.db.base import Base
from app.infrastructure.id_generator import SequentialGenerator
from app.infrastructure.db.models import *  # noqa: F401, F403

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "postgres: requires PostgreSQL integration test"
    )


requires_postgres = pytest.mark.skipif(
    not bool(TEST_DB_URL),
    reason="Requires TEST_DATABASE_URL pointing to a PostgreSQL database",
)


@pytest_asyncio.fixture
async def db_engine():
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES ('default', 'Default Tenant') ON CONFLICT (id) DO NOTHING")
            )
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES ('tenant-b', 'Tenant B') ON CONFLICT (id) DO NOTHING")
            )
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with async_session() as session:
        for table in [
            "outbox_messages",
            "session_events",
            "daily_usage",
            "sessions",
            "policies",
            "idempotency_keys",
        ]:
            await session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
def fixed_clock():
    return FixedClock(datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc))


@pytest.fixture
def seq_id_generator():
    return SequentialGenerator(prefix="test")
