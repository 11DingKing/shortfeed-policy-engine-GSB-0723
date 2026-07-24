"""Tests for the deterministic historical replay service."""
from __future__ import annotations

import pytest

from spe.domain.reason_codes import ReasonCode


async def _publish(container, tenant, doc, key):
    r = await container.policies.create_policy(
        tenant_id=tenant, document=doc, idempotency_key=key
    )
    assert r.ok, r
    return r.data["policy_id"]


@pytest.mark.asyncio
async def test_replay_reconstructs_counters(container, sample_policy, fake_clock):
    tenant = "rp"
    policy_id = await _publish(container, tenant, sample_policy, "rp-create")
    start = await container.sessions.start(
        tenant_id=tenant, user_id="u", policy_id=policy_id,
        idempotency_key="s", user_age=20, user_timezone="UTC",
    )
    sid = start.data["session_id"]

    fake_clock.advance(60)
    await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=1)
    fake_clock.advance(60)
    await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=2)
    fake_clock.advance(30)
    await container.sessions.end(tenant_id=tenant, session_id=sid)

    replay = await container.replays.replay(tenant_id=tenant, session_id=sid)
    assert replay.ok
    assert replay.reason is ReasonCode.REPLAY_COMPLETED
    steps = replay.data["steps"]
    # At minimum: started, hb1, hb2, ended.
    kinds = [s["event"] for s in steps]
    assert "session.started" in kinds
    assert "session.heartbeat" in kinds
    assert "session.ended" in kinds
    assert abs(replay.data["total_session_seconds"] - 150) < 1e-3  # 60+60+30


@pytest.mark.asyncio
async def test_replay_uses_pinned_version_after_new_publish(container, sample_policy, fake_clock):
    tenant = "rpv"
    policy_id = await _publish(container, tenant, sample_policy, "rpv-create")
    start = await container.sessions.start(
        tenant_id=tenant, user_id="u", policy_id=policy_id,
        idempotency_key="s", user_age=20, user_timezone="UTC",
    )
    sid = start.data["session_id"]
    pinned = start.data["policy_version"]

    # Publish a new version after session started.
    await container.policies.publish_version(
        tenant_id=tenant, policy_id=policy_id,
        document={"name": "new", "rules": [
            {"id": "deny-all", "when": {"op": "true"},
             "effect": {"kind": "deny", "reason_code": "DENIED_NO_APPROVAL"}}
        ]},
    )

    fake_clock.advance(30)
    hb = await container.sessions.heartbeat(tenant_id=tenant, session_id=sid, sequence=1)
    assert hb.ok  # old policy version (allow-all) still applies

    replay = await container.replays.replay(tenant_id=tenant, session_id=sid)
    assert replay.data["pinned_policy_version"] == pinned
    # Replay's heartbeat decision must match live decision (ALLOWED).
    hb_steps = [s for s in replay.data["steps"] if s["event"] == "session.heartbeat"]
    assert hb_steps, "expected heartbeat step in replay"
    assert hb_steps[0]["decision"]["allowed"] is True
