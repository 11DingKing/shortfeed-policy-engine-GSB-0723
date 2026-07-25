"""Shared pytest fixtures.

Tests run against two backends:

* **SQLite (in-memory / on-disk)** for fast, deterministic unit and API tests;
* **real PostgreSQL** for the invariants the spec requires to be proven on the
  production engine — version pinning, tenant isolation, cross-day settlement,
  process restart, concurrency and Alembic migrations.

All tests use a deterministic :class:`FixedClock` and
:class:`SequentialIdGenerator`, so ids, traces and timestamps are reproducible.
The Postgres fixtures are skipped automatically when no database is reachable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spe.app import create_app
from spe.config import Settings
from spe.container import Container
from spe.domain.clock import FixedClock
from spe.domain.ids import SequentialIdGenerator
from spe.infra.db.base import Base
from spe.infra.db.ddl import CREATE_ACTIVE_SESSION_INDEX

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

# A fixed reference instant: 2026-07-24 10:00 UTC.
REFERENCE = datetime(2026, 7, 24, 10, 0, 0, tzinfo=UTC)

# Real PostgreSQL used for the "must be proven on Postgres" invariants. Override
# with SPE_TEST_DATABASE_URL; defaults to the local test container.
PG_URL = os.environ.get(
    "SPE_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:55439/spe_test",
)

_ALL_TABLES = (
    "daily_usage_ledger",
    "heartbeats",
    "outbox",
    "sessions",
    "policies",
)


def birth_for_age(age: int, ref: datetime = REFERENCE) -> date:
    """Return a birth date that makes the user exactly ``age`` at ``ref`` (UTC)."""
    d = ref.date()
    return date(d.year - age, d.month, d.day)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(REFERENCE)


@pytest.fixture
def container(clock: FixedClock) -> Container:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", heartbeat_max_gap_seconds=90)
    return Container(settings=settings, clock=clock, ids=SequentialIdGenerator("id"))


@pytest_asyncio.fixture
async def initialized_container(container: Container) -> AsyncIterator[Container]:
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
    yield container
    await container.dispose()


@pytest_asyncio.fixture
async def client(initialized_container: Container) -> AsyncIterator[AsyncClient]:
    # Build the app but bypass its lifespan (which would dispose the container);
    # the initialized_container fixture owns the engine lifecycle here.
    app = create_app(initialized_container)
    app.state.container = initialized_container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def file_client(tmp_path, clock: FixedClock) -> AsyncIterator[AsyncClient]:
    """A client backed by an on-disk SQLite database.

    Unlike the in-memory ``StaticPool`` client, this gives every request its own
    real connection with independent transactions — required to exercise genuine
    concurrent-write behaviour against the single-active-session DB constraint.
    """
    db_file = tmp_path / "concurrency.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_file}", heartbeat_max_gap_seconds=90
    )
    container = Container(settings=settings, clock=clock, ids=SequentialIdGenerator("id"))
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
    app = create_app(container)
    app.state.container = container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await container.dispose()


async def _pg_reachable() -> bool:
    """Return whether the target Postgres database is usable.

    If the server is up but the target database does not yet exist, create it so
    the suite is self-provisioning.
    """
    try:
        engine = create_async_engine(PG_URL)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        pass
    # Try to create the database via the admin ('postgres') database.
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(PG_URL)
        db_name = parts.path.lstrip("/")
        admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
        engine = create_async_engine(admin, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def pg_schema() -> AsyncIterator[str]:
    """Create the schema on the real Postgres once, truncating between tests."""
    if not await _pg_reachable():
        pytest.skip(f"PostgreSQL not reachable at {PG_URL}")
    engine = create_async_engine(PG_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
        await conn.execute(
            text("TRUNCATE " + ", ".join(_ALL_TABLES) + " RESTART IDENTITY CASCADE")
        )
    await engine.dispose()
    yield PG_URL


def make_pg_container(clock: FixedClock, ids_prefix: str = "id") -> Container:
    """Build a container bound to the real Postgres (fresh engine = 'new process')."""
    settings = Settings(database_url=PG_URL, heartbeat_max_gap_seconds=90)
    return Container(settings=settings, clock=clock, ids=SequentialIdGenerator(ids_prefix))


@pytest_asyncio.fixture
async def pg_client(pg_schema: str, clock: FixedClock) -> AsyncIterator[AsyncClient]:
    """An HTTP client backed by the real PostgreSQL database."""
    container = make_pg_container(clock)
    app = create_app(container)
    app.state.container = container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await container.dispose()


def headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id}


def sample_policy(
    name: str = "default",
    timezone: str = "UTC",
    min_age: int | None = 13,
    daily_limit_seconds: int | None = 3600,
    session_limit_seconds: int | None = 1800,
    bedtime: tuple[str, str] | None = ("22:00:00", "06:00:00"),
    exceptions: list[dict] | None = None,
) -> dict:
    """Build a policy document payload for tests."""
    rules: dict = {"timezone": timezone, "exceptions": exceptions or []}
    if min_age is not None:
        rules["age_gate"] = {"kind": "age_gate", "min_age": min_age}
    if daily_limit_seconds is not None:
        rules["daily_limit"] = {"kind": "daily_limit", "max_seconds": daily_limit_seconds}
    if session_limit_seconds is not None:
        rules["session_limit"] = {"kind": "session_limit", "max_seconds": session_limit_seconds}
    if bedtime is not None:
        rules["bedtime"] = {
            "kind": "bedtime",
            "windows": [{"start": bedtime[0], "end": bedtime[1]}],
        }
    return {"name": name, "rules": rules}
