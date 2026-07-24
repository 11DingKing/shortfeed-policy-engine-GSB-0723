"""Core invariant tests: idempotency, concurrency, ordering, isolation,
cross-midnight settlement, version pinning and replay."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import TENANT_A, TENANT_B, headers, sample_policy

pytestmark = pytest.mark.asyncio


async def _publish(client, tenant: str = TENANT_A, **kw) -> int:
    resp = await client.post(
        "/v1/policies", json={"document": sample_policy(**kw)}, headers=headers(tenant)
    )
    assert resp.status_code == 201
    return resp.json()["version"]


async def test_idempotent_start_returns_same_session(client) -> None:
    await _publish(client, bedtime=None)
    first = await client.post(
        "/v1/sessions",
        json={"user_id": "u1", "user_age": 20, "idempotency_key": "k1"},
        headers=headers(),
    )
    second = await client.post(
        "/v1/sessions",
        json={"user_id": "u1", "user_age": 20, "idempotency_key": "k1"},
        headers=headers(),
    )
    assert first.json()["session"]["id"] == second.json()["session"]["id"]
    assert second.json()["reason"] == "SESSION_STARTED_IDEMPOTENT"


async def test_second_active_session_conflict(client) -> None:
    await _publish(client, bedtime=None)
    first = await client.post(
        "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers()
    )
    assert first.status_code == 200
    second = await client.post(
        "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers()
    )
    assert second.status_code == 409
    assert second.json()["detail"]["reason"] == "REJECTED_ACTIVE_SESSION_EXISTS"


async def test_concurrent_starts_only_one_wins(file_client) -> None:
    await _publish(file_client, bedtime=None)
    results = await asyncio.gather(
        *[
            file_client.post(
                "/v1/sessions", json={"user_id": "race", "user_age": 20}, headers=headers()
            )
            for _ in range(5)
        ]
    )
    statuses = sorted(r.status_code for r in results)
    # Exactly one 200 success; the rest are 409 conflicts.
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4


async def test_stale_and_out_of_order_heartbeats_ignored(client) -> None:
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (
        await client.post(
            "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers()
        )
    ).json()["session"]["id"]

    async def hb(seq: int, total: int):
        return await client.post(
            f"/v1/sessions/{sid}/heartbeat",
            json={"seq": seq, "watched_seconds_total": total},
            headers=headers(),
        )

    assert (await hb(1, 30)).json()["reason"] == "HEARTBEAT_APPLIED"
    assert (await hb(3, 90)).json()["reason"] == "HEARTBEAT_APPLIED"
    # Out-of-order (seq 2 < last seq 3) -> ignored.
    late = await hb(2, 60)
    assert late.json()["reason"] == "HEARTBEAT_IGNORED_STALE"
    # Duplicate (seq 3) -> ignored.
    dup = await hb(3, 90)
    assert dup.json()["reason"] == "HEARTBEAT_IGNORED_STALE"

    usage = await client.get(f"/v1/sessions/{sid}/usage", headers=headers())
    # Only seq1 (30) + seq3 delta (90-30=60) credited == 90; clamped per beat at 90.
    assert usage.json()["session"]["total_watched_seconds"] == 90


async def test_tenant_isolation(client) -> None:
    await _publish(client, tenant=TENANT_A, bedtime=None)
    await _publish(client, tenant=TENANT_B, bedtime=None)
    sid = (
        await client.post(
            "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers(TENANT_A)
        )
    ).json()["session"]["id"]

    # Tenant B cannot see tenant A's session.
    resp = await client.get(f"/v1/sessions/{sid}/usage", headers=headers(TENANT_B))
    assert resp.status_code == 404


async def test_cross_midnight_settlement(client, clock) -> None:
    # America/New_York: local midnight is 04:00 UTC (July, UTC-4).
    await _publish(
        client,
        timezone="America/New_York",
        daily_limit_seconds=80000,
        session_limit_seconds=80000,
        bedtime=None,
    )
    sid = (
        await client.post(
            "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers()
        )
    ).json()["session"]["id"]

    from datetime import UTC, datetime

    # 03:30 UTC == 23:30 local on 07-23.
    clock.set(datetime(2026, 7, 24, 3, 30, tzinfo=UTC))
    await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 1, "watched_seconds_total": 60},
        headers=headers(),
    )
    # 04:30 UTC == 00:30 local on 07-24 (next local day).
    clock.set(datetime(2026, 7, 24, 4, 30, tzinfo=UTC))
    await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 2, "watched_seconds_total": 120},
        headers=headers(),
    )

    usage = (await client.get(f"/v1/sessions/{sid}/usage", headers=headers())).json()
    daily = usage["session"]["daily"]
    # Two distinct local days each credited 60s.
    assert daily == {"2026-07-23": 60, "2026-07-24": 60}


async def test_version_pinning_does_not_change_history(client) -> None:
    # Publish v1 with a generous session limit; start a session pinned to v1.
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (
        await client.post(
            "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers()
        )
    ).json()["session"]["id"]

    # Publish v2 with a tiny session limit.
    await _publish(
        client, name="v2", session_limit_seconds=5, daily_limit_seconds=10000, bedtime=None
    )

    # The in-flight session still honours v1's generous limit.
    resp = await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 1, "watched_seconds_total": 90},
        headers=headers(),
    )
    body = resp.json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["session"]["policy_version"] == 1
    assert body["session"]["total_watched_seconds"] == 90


async def test_replay_reproduces_settlement(client) -> None:
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (
        await client.post(
            "/v1/sessions", json={"user_id": "u1", "user_age": 20}, headers=headers()
        )
    ).json()["session"]["id"]
    for seq, total in [(1, 30), (2, 70), (3, 120)]:
        await client.post(
            f"/v1/sessions/{sid}/heartbeat",
            json={"seq": seq, "watched_seconds_total": total},
            headers=headers(),
        )

    replay = await client.get(f"/v1/sessions/{sid}/replay", headers=headers())
    assert replay.status_code == 200
    steps = replay.json()["steps"]
    assert [s["seq"] for s in steps] == [1, 2, 3]
    assert steps[-1]["total_watched_seconds"] == 120
    assert all(s["allowed"] for s in steps)
