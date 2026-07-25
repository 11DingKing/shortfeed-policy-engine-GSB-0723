"""Invariants proven against real PostgreSQL.

These mirror the SQLite invariant tests but run on the production engine, where
row locks (``SELECT ... FOR UPDATE``) and ``INSERT ... ON CONFLICT`` upserts
behave for real. They cover the properties the spec requires to be demonstrated
on Postgres: version pinning, tenant isolation, cross-day settlement, process
restart, and concurrent heartbeats / starts remaining consistent (including the
outbox written in the same transaction).

All tests are skipped automatically when PostgreSQL is not reachable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spe.app import create_app
from tests.conftest import (
    TENANT_A,
    TENANT_B,
    birth_for_age,
    headers,
    make_pg_container,
    sample_policy,
)

pytestmark = pytest.mark.asyncio


async def _publish(client, tenant: str = TENANT_A, **kw) -> int:
    resp = await client.post(
        "/v1/policies", json={"document": sample_policy(**kw)}, headers=headers(tenant)
    )
    assert resp.status_code == 201
    return resp.json()["version"]


async def _start(client, tenant: str = TENANT_A, user_id: str = "u1", age: int = 20, **kw):
    return await client.post(
        "/v1/sessions",
        json={"user_id": user_id, "birth_date": birth_for_age(age).isoformat(), **kw},
        headers=headers(tenant),
    )


async def _hb(client, sid: str, seq: int, total: int, tenant: str = TENANT_A):
    return await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": seq, "watched_seconds_total": total},
        headers=headers(tenant),
    )


async def test_pg_version_pinning(pg_client) -> None:
    await _publish(pg_client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (await _start(pg_client)).json()["session"]["id"]
    await _publish(
        pg_client, name="v2", session_limit_seconds=5, daily_limit_seconds=10000, bedtime=None
    )
    resp = await _hb(pg_client, sid, 1, 90)
    body = resp.json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["session"]["policy_version"] == 1
    assert body["session"]["total_watched_seconds"] == 90


async def test_pg_tenant_isolation(pg_client) -> None:
    await _publish(pg_client, tenant=TENANT_A, bedtime=None)
    await _publish(pg_client, tenant=TENANT_B, bedtime=None)
    sid = (await _start(pg_client, tenant=TENANT_A)).json()["session"]["id"]
    resp = await pg_client.get(f"/v1/sessions/{sid}/usage", headers=headers(TENANT_B))
    assert resp.status_code == 404


async def test_pg_cross_day_ledger_settlement(pg_client, clock) -> None:
    # America/New_York local midnight == 04:00 UTC in July.
    await _publish(
        pg_client,
        timezone="America/New_York",
        daily_limit_seconds=80000,
        session_limit_seconds=80000,
        bedtime=None,
    )
    sid = (await _start(pg_client)).json()["session"]["id"]
    clock.set(datetime(2026, 7, 24, 4, 0, 30, tzinfo=UTC))
    resp = await _hb(pg_client, sid, 1, 60)
    assert resp.json()["extra"]["per_day"] == {"2026-07-23": 30, "2026-07-24": 30}


async def test_pg_daily_ledger_survives_process_restart(pg_schema, clock) -> None:
    """A brand-new container (fresh engine == new process) sees the persisted ledger."""
    # --- process 1: publish + consume 60s, then end the session ---
    c1 = make_pg_container(clock)
    app1 = create_app(c1)
    app1.state.container = c1
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://t") as client:
        await _publish(
            client, daily_limit_seconds=100, session_limit_seconds=100, bedtime=None, min_age=None
        )
        s1 = (await _start(client)).json()["session"]["id"]
        await _hb(client, s1, 1, 60)
        await client.post(f"/v1/sessions/{s1}/end", headers=headers())
    await c1.dispose()

    # --- process 2: new container/engine; ledger must still show 60s consumed ---
    c2 = make_pg_container(clock)
    app2 = create_app(c2)
    app2.state.container = c2
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://t") as client:
        s2 = (await _start(client)).json()["session"]["id"]
        # Only 40s remain of the 100s daily budget.
        resp = await _hb(client, s2, 1, 60)
        body = resp.json()
        assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
        assert body["extra"]["limit_reason"] == "DENIED_DAILY_LIMIT_REACHED"
        assert body["extra"]["credited_seconds"] == 40
    await c2.dispose()


async def test_pg_concurrent_starts_only_one_wins(pg_client) -> None:
    await _publish(pg_client, bedtime=None)
    results = await asyncio.gather(
        *[_start(pg_client, user_id="race") for _ in range(6)]
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses.count(200) == 1
    assert statuses.count(409) == 5


async def test_pg_concurrent_heartbeats_no_double_count(pg_schema, clock) -> None:
    """Concurrent heartbeats serialise via row lock; usage and outbox stay consistent."""
    container = make_pg_container(clock)
    app = create_app(container)
    app.state.container = container
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        await _publish(
            client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None
        )
        sid = (await _start(client)).json()["session"]["id"]

        # Fire distinct sequence numbers concurrently with a common cumulative marker
        # growth. The row lock serialises them; each in-order beat credits its delta
        # once, stale ones credit nothing.
        await asyncio.gather(
            *[_hb(client, sid, seq, seq * 10) for seq in range(1, 6)]
        )
        usage = await client.get(f"/v1/sessions/{sid}/usage", headers=headers())
        total = usage.json()["session"]["total_watched_seconds"]
        # Highest accepted cumulative marker is 50; total credited cannot exceed it.
        assert total == 50

    # Outbox rows were written in the same transactions as the state changes.
    engine = create_async_engine(pg_schema)
    async with engine.connect() as conn:
        events = (
            await conn.execute(text("SELECT COUNT(*) FROM outbox WHERE event_type LIKE 'session%'"))
        ).scalar()
    await engine.dispose()
    assert events >= 1
    await container.dispose()
