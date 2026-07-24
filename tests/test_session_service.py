"""Integration tests for the session service against a real SQLite database."""
from __future__ import annotations

from datetime import UTC

import pytest

from spe.domain.reason_codes import ReasonCode


async def _publish(container, tenant, policy_doc, author="tester"):
    created = await container.policies.create_policy(
        tenant_id=tenant, document=policy_doc, author=author, idempotency_key=f"create-{policy_doc['name']}"
    )
    assert created.ok, created
    return created.data["policy_id"]


@pytest.mark.asyncio
async def test_full_lifecycle_with_teen_cap(container, sample_policy, fake_clock):
    tenant = "tenant-acme"
    policy_id = await _publish(container, tenant, sample_policy)

    # Start a teen session.
    start = await container.sessions.start(
        tenant_id=tenant,
        user_id="user-teen",
        policy_id=policy_id,
        idempotency_key="start-1",
        user_age=15,
        user_timezone="Asia/Shanghai",
    )
    assert start.ok, start
    sid = start.data["session_id"]

    # 15 heartbeats of 60s each -> session cap at 15 min should fire on the 15th.
    reasons = []
    for i in range(1, 16):
        fake_clock.advance(60)
        hb = await container.sessions.heartbeat(
            tenant_id=tenant, session_id=sid, sequence=i
        )
        reasons.append(hb.reason)
        assert hb.ok or hb.reason in (ReasonCode.LIMITED_SESSION_QUOTA_REACHED,), hb.data

    assert reasons[-1] is ReasonCode.LIMITED_SESSION_QUOTA_REACHED
    # The final heartbeat also ended the session.
    assert hb.data["state"] == "ENDED"


@pytest.mark.asyncio
async def test_pause_and_resume_skips_time(container, sample_policy, fake_clock):
    tenant = "tenant-acme"
    policy_id = await _publish(container, tenant, sample_policy)

    start = await container.sessions.start(
        tenant_id=tenant, user_id="user-a", policy_id=policy_id,
        idempotency_key="start-a", user_age=20, user_timezone="UTC",
    )
    sid = start.data["session_id"]

    fake_clock.advance(60)
    await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=1)

    fake_clock.advance(60)
    pause = await container.sessions.pause(tenant_id=tenant, session_id=sid)
    assert pause.reason is ReasonCode.SESSION_PAUSED

    # Long pause — should NOT accumulate.
    fake_clock.advance(60 * 60)
    resume = await container.sessions.resume(tenant_id=tenant, session_id=sid)
    assert resume.reason is ReasonCode.SESSION_RESUMED

    fake_clock.advance(60)
    hb = await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=2)
    assert hb.ok
    assert abs(hb.data["accumulated_active_seconds"] - 180) < 1e-3  # 60 + 60 + 60


@pytest.mark.asyncio
async def test_start_idempotent(container, sample_policy, fake_clock):
    tenant = "t"
    policy_id = await _publish(container, tenant, sample_policy)
    args = dict(
        tenant_id=tenant, user_id="u1", policy_id=policy_id,
        idempotency_key="idem-1", user_age=20, user_timezone="UTC",
    )
    r1 = await container.sessions.start(**args)
    r2 = await container.sessions.start(**args)
    assert r1.ok and r2.ok
    assert r2.reason is ReasonCode.IDEMPOTENT_REPLAY
    assert r1.data["session_id"] == r2.data["session_id"]


@pytest.mark.asyncio
async def test_concurrent_start_blocked_by_db(container, sample_policy, fake_clock):
    tenant = "t"
    policy_id = await _publish(container, tenant, sample_policy)
    r1 = await container.sessions.start(
        tenant_id=tenant, user_id="u-conc", policy_id=policy_id,
        idempotency_key="k1", user_age=20, user_timezone="UTC",
    )
    assert r1.ok
    r2 = await container.sessions.start(
        tenant_id=tenant, user_id="u-conc", policy_id=policy_id,
        idempotency_key="k2", user_age=20, user_timezone="UTC",
    )
    assert not r2.ok
    assert r2.reason is ReasonCode.CONCURRENT_SESSION_BLOCKED


@pytest.mark.asyncio
async def test_tenant_isolation(container, sample_policy, fake_clock):
    p1 = await _publish(container, "tenant-a", sample_policy)
    # Use the same user_id in tenant-b and ensure no cross-tenant session.
    r = await container.sessions.start(
        tenant_id="tenant-b", user_id="u1", policy_id=p1,
        idempotency_key="k1", user_age=20, user_timezone="UTC",
    )
    # policy belongs to tenant-a, so tenant-b should not see it.
    assert not r.ok
    assert r.reason is ReasonCode.POLICY_NOT_FOUND


@pytest.mark.asyncio
async def test_new_policy_version_does_not_affect_running_session(container, sample_policy, fake_clock):
    tenant = "tv"
    policy_id = await _publish(container, tenant, sample_policy)

    start = await container.sessions.start(
        tenant_id=tenant, user_id="u", policy_id=policy_id,
        idempotency_key="s", user_age=25, user_timezone="UTC",
    )
    sid = start.data["session_id"]
    pinned_version = start.data["policy_version"]

    # Publish a much stricter version (age 25+ denied).
    stricter = {
        "name": "strict",
        "rules": [
            {"id": "no-adults", "when": {"op": "age_gte", "n": 25},
             "effect": {"kind": "deny", "reason_code": "DENIED_AGE_ABOVE_MAXIMUM"}},
            {"id": "allow", "when": {"op": "true"}, "effect": {"kind": "allow"}},
        ],
    }
    pub = await container.policies.publish_version(
        tenant_id=tenant, policy_id=policy_id, document=stricter
    )
    assert pub.ok
    assert pub.data["version"] != int(pinned_version)

    # Heartbeat on the old session still allowed (pinned policy).
    fake_clock.advance(30)
    hb = await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=1)
    assert hb.ok
    assert hb.reason in (ReasonCode.HEARTBEAT_ACCEPTED, ReasonCode.CROSS_MIDNIGHT_DAILY_RESET)


@pytest.mark.asyncio
async def test_cross_midnight_daily_reset(container, fake_clock):
    tenant = "cm"
    doc = {
        "name": "simple-daily",
        "rules": [
            {"id": "daily-1h",
             "when": {"op": "daily_used_gte", "seconds": 3600},
             "effect": {"kind": "limit_daily", "seconds": 3600}},
            {"id": "allow", "when": {"op": "true"}, "effect": {"kind": "allow"}},
        ],
    }
    policy_id = await _publish(container, tenant, doc)
    # Start at 23:50 Shanghai time.
    from datetime import datetime
    fake_clock.set(datetime(2026, 6, 1, 15, 50, 0, tzinfo=UTC))  # 23:50 Shanghai

    start = await container.sessions.start(
        tenant_id=tenant, user_id="u", policy_id=policy_id,
        idempotency_key="s", user_age=30, user_timezone="Asia/Shanghai",
    )
    sid = start.data["session_id"]

    # 20 min heartbeat crosses midnight.
    fake_clock.advance(20 * 60)
    hb = await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=1)
    assert hb.ok
    assert hb.reason is ReasonCode.CROSS_MIDNIGHT_DAILY_RESET

    # Query usage: the new day should have ~10 minutes, the previous day ~10.
    usage = await container.sessions.query_usage(
        tenant_id=tenant, user_id="u", user_timezone="Asia/Shanghai"
    )
    assert usage.ok
    assert usage.data["daily_used_seconds"] < 20 * 60  # only 10 min on new day
