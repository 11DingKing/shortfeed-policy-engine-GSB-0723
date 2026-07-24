"""Alembic migration tests against real PostgreSQL.

The spec requires migrations to be proven on PostgreSQL, including a zero-downtime
(additive) upgrade and a working rollback. These tests:

* apply the full chain from an empty database (``upgrade head``);
* prove the ledger migration (0004) is *data-preserving*: per-session daily usage
  recorded under the old schema is folded into the authoritative per-user ledger,
  so a user's consumed quota survives the cutover;
* prove the chain is reversible (``downgrade base``) and re-appliable.

Each test uses its own throwaway database so runs are isolated and can execute
concurrently. Skipped automatically when PostgreSQL is not reachable.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.conftest import PG_URL

ROOT = Path(__file__).resolve().parents[1]


async def _pg_reachable(url: str) -> bool:
    try:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


def _admin_url() -> str:
    parts = urlsplit(PG_URL)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))


def _with_db(name: str) -> str:
    parts = urlsplit(PG_URL)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))


async def _create_db(name: str) -> None:
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


async def _drop_db(name: str) -> None:
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    await engine.dispose()


@pytest.fixture
def migration_db(monkeypatch):
    """Provision a throwaway Postgres database and point Alembic/settings at it."""
    if not asyncio.run(_pg_reachable(_admin_url())):
        pytest.skip(f"PostgreSQL not reachable at {PG_URL}")

    db_name = f"spe_mig_{uuid.uuid4().hex[:12]}"
    asyncio.run(_create_db(db_name))
    url = _with_db(db_name)

    monkeypatch.setenv("SPE_DATABASE_URL", url)
    from spe.config import get_settings

    get_settings.cache_clear()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["url"] = url
    try:
        yield cfg, url
    finally:
        asyncio.run(_drop_db(db_name))
        get_settings.cache_clear()


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        result = {r[0] for r in rows}
    await engine.dispose()
    return result


def test_upgrade_head_creates_ledger_schema(migration_db) -> None:
    cfg, url = migration_db
    command.upgrade(cfg, "head")
    tables = asyncio.run(_tables(url))
    assert {"policies", "sessions", "heartbeats", "outbox", "daily_usage_ledger"} <= tables
    # The old per-session usage table has been retired by migration 0004.
    assert "session_daily_usage" not in tables


def test_ledger_migration_preserves_consumed_quota(migration_db) -> None:
    cfg, url = migration_db

    # Upgrade to just before the ledger migration (old schema still present).
    command.upgrade(cfg, "0003_session_birth_date")

    async def seed() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            # Two sessions for the same user on the same local day, 40s + 30s.
            for sid in ("s1", "s2"):
                await conn.execute(
                    text(
                        "INSERT INTO sessions (id, tenant_id, user_id, policy_id,"
                        " policy_version, status, birth_date, started_at, updated_at,"
                        " last_seq, watched_seconds_marker, total_watched_seconds)"
                        " VALUES (:id, 't', 'u', 'p', 1, 'ENDED', '2010-01-01',"
                        " now(), now(), 0, 0, 0)"
                    ),
                    {"id": sid},
                )
            await conn.execute(
                text(
                    "INSERT INTO session_daily_usage (session_id, tenant_id, local_day,"
                    " seconds) VALUES ('s1', 't', '2026-07-24', 40),"
                    " ('s2', 't', '2026-07-24', 30)"
                )
            )
        await engine.dispose()

    async def read_total() -> int:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            value = (
                await conn.execute(
                    text(
                        "SELECT seconds FROM daily_usage_ledger WHERE tenant_id='t'"
                        " AND user_id='u' AND local_day='2026-07-24'"
                    )
                )
            ).scalar()
        await engine.dispose()
        return value

    asyncio.run(seed())
    # Apply the ledger migration synchronously (folds usage into the per-user ledger).
    command.upgrade(cfg, "0004_daily_usage_ledger")
    # 40 + 30 summed into one authoritative per-user ledger row.
    assert asyncio.run(read_total()) == 70


def test_downgrade_base_then_reupgrade(migration_db) -> None:
    cfg, url = migration_db
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    tables = asyncio.run(_tables(url))
    assert "sessions" not in tables
    assert "daily_usage_ledger" not in tables
    # Re-upgrading from the clean state works.
    command.upgrade(cfg, "head")
    tables = asyncio.run(_tables(url))
    assert "daily_usage_ledger" in tables


# --- Reversible round-trip with data --------------------------------------

_SESSIONS = [
    # (id, tenant, user, started_at)  — multiple sessions per user, multi-tenant.
    ("t1u1s1", "t1", "u1", "2026-07-20T08:00:00+00:00"),
    ("t1u1s2", "t1", "u1", "2026-07-22T08:00:00+00:00"),  # most recent for (t1,u1)
    ("t1u2s1", "t1", "u2", "2026-07-21T08:00:00+00:00"),
    ("t2u1s1", "t2", "u1", "2026-07-21T08:00:00+00:00"),  # same user id, other tenant
]

# Per-session daily buckets seeded under the 0003 schema:
#   (session_id, local_day, seconds)  — same user spans days & sessions.
_BUCKETS = [
    ("t1u1s1", "2026-07-20", 40),
    ("t1u1s1", "2026-07-21", 25),
    ("t1u1s2", "2026-07-21", 15),  # (t1,u1,07-21) total = 25 + 15 = 40
    ("t1u1s2", "2026-07-22", 50),
    ("t1u2s1", "2026-07-21", 70),
    ("t2u1s1", "2026-07-21", 90),  # tenant-isolated from t1/u1
]

# Expected authoritative per-(tenant,user,day) ledger after the forward fold.
_EXPECTED_LEDGER = {
    ("t1", "u1", "2026-07-20"): 40,
    ("t1", "u1", "2026-07-21"): 40,
    ("t1", "u1", "2026-07-22"): 50,
    ("t1", "u2", "2026-07-21"): 70,
    ("t2", "u1", "2026-07-21"): 90,
}


async def _seed_0003(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for sid, tenant, user, started in _SESSIONS:
            await conn.execute(
                text(
                    "INSERT INTO sessions (id, tenant_id, user_id, policy_id,"
                    " policy_version, status, birth_date, started_at, updated_at,"
                    " last_seq, watched_seconds_marker, total_watched_seconds)"
                    " VALUES (:id, :t, :u, 'p', 1, 'ENDED', '2010-01-01',"
                    " :started, :started, 0, 0, 0)"
                ),
                {
                    "id": sid,
                    "t": tenant,
                    "u": user,
                    "started": datetime.fromisoformat(started),
                },
            )
        for sid, day, secs in _BUCKETS:
            tenant = next(t for i, t, _u, _s in _SESSIONS if i == sid)
            await conn.execute(
                text(
                    "INSERT INTO session_daily_usage (session_id, tenant_id,"
                    " local_day, seconds) VALUES (:sid, :t, :day, :secs)"
                ),
                {"sid": sid, "t": tenant, "day": day, "secs": secs},
            )
    await engine.dispose()


async def _ledger_totals(url: str) -> dict[tuple[str, str, str], int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tenant_id, user_id, local_day, seconds FROM daily_usage_ledger"
            )
        )
        result = {(r[0], r[1], r[2]): r[3] for r in rows}
    await engine.dispose()
    return result


async def _legacy_totals(url: str) -> dict[tuple[str, str, str], int]:
    """Per-(tenant,user,day) totals as seen through the 0003-era table."""
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT s.tenant_id, s.user_id, d.local_day, SUM(d.seconds)"
                " FROM session_daily_usage d JOIN sessions s ON s.id = d.session_id"
                " GROUP BY s.tenant_id, s.user_id, d.local_day"
            )
        )
        result = {(r[0], r[1], r[2]): r[3] for r in rows}
    await engine.dispose()
    return result


async def _add_new_usage(url: str, tenant: str, user: str, day: str, secs: int) -> None:
    """Simulate new-code usage: upsert into the authoritative ledger."""
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO daily_usage_ledger (tenant_id, user_id, local_day, seconds)"
                " VALUES (:t, :u, :day, :secs)"
                " ON CONFLICT (tenant_id, user_id, local_day)"
                " DO UPDATE SET seconds = daily_usage_ledger.seconds + :secs"
            ),
            {"t": tenant, "u": user, "day": day, "secs": secs},
        )
    await engine.dispose()


def test_ledger_roundtrip_preserves_usage_with_data(migration_db) -> None:
    """0003 -> 0004 -> 0003 -> 0004 with realistic data must never drop or double-count.

    Covers multiple historical sessions per user, multiple dates, multiple tenants
    (including the same user id across tenants), new usage added after the first
    upgrade, and a repeated upgrade/rollback cycle.
    """
    cfg, url = migration_db

    # Seed under the 0003 (pre-ledger) schema and confirm the old code path reads it.
    command.upgrade(cfg, "0003_session_birth_date")
    asyncio.run(_seed_0003(url))
    assert asyncio.run(_legacy_totals(url)) == _EXPECTED_LEDGER

    # Forward: fold into the authoritative per-user ledger.
    command.upgrade(cfg, "0004_daily_usage_ledger")
    assert asyncio.run(_ledger_totals(url)) == _EXPECTED_LEDGER

    # New-code writes more usage after the cutover (existing day + a brand new day).
    asyncio.run(_add_new_usage(url, "t1", "u1", "2026-07-22", 20))  # 50 -> 70
    asyncio.run(_add_new_usage(url, "t1", "u1", "2026-07-25", 30))  # new day
    after_new = dict(_EXPECTED_LEDGER)
    after_new[("t1", "u1", "2026-07-22")] = 70
    after_new[("t1", "u1", "2026-07-25")] = 30
    assert asyncio.run(_ledger_totals(url)) == after_new

    # Rollback: 0004 -> 0003 must retain consumed quota for the old code path.
    command.downgrade(cfg, "0003_session_birth_date")
    assert asyncio.run(_legacy_totals(url)) == after_new

    # Re-upgrade: 0003 -> 0004 again must reproduce exactly (no loss, no doubling).
    command.upgrade(cfg, "0004_daily_usage_ledger")
    assert asyncio.run(_ledger_totals(url)) == after_new

    # A second full cycle stays stable.
    command.downgrade(cfg, "0003_session_birth_date")
    command.upgrade(cfg, "0004_daily_usage_ledger")
    assert asyncio.run(_ledger_totals(url)) == after_new
