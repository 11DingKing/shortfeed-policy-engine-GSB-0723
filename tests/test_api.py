"""End-to-end HTTP API tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from spe.app import create_app
from spe.config import Settings
from spe.container import build_container
from spe.domain.clock import FakeClock
from spe.domain.ids import SequentialIdGenerator


@pytest.fixture
async def app_fixture(tmp_path):
    clock = FakeClock(datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    ids = SequentialIdGenerator()
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path/'api.db'}",
        outbox_poll_interval_seconds=1000.0,
    )
    container = build_container(settings, clock=clock, id_gen=ids)
    app = create_app(settings, container=container)
    app.state.container = container
    await container.startup()
    try:
        yield app, clock
    finally:
        await container.shutdown()


@pytest.mark.asyncio
async def test_healthz(app_fixture):
    app, _ = app_fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/healthz")
        assert r.status_code == 200
        assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_reason_codes_catalog(app_fixture):
    app, _ = app_fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/reason-codes")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "SESSION_STARTED" in data
        assert "LIMITED_DAILY_QUOTA_REACHED" in data


@pytest.mark.asyncio
async def test_validate_policy_endpoint(app_fixture, sample_policy):
    app, _ = app_fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/policies/validate",
            headers={"X-Tenant-Id": "t1"},
            json={"document": sample_policy},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_preview_returns_trace(app_fixture, sample_policy):
    app, _ = app_fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/policies/preview",
            headers={"X-Tenant-Id": "t1"},
            json={
                "document": sample_policy,
                "context": {
                    "user_age": 10,
                    "user_timezone": "UTC",
                    "now_utc": "2026-06-01T10:00:00+00:00",
                    "daily_used_seconds": 0,
                    "session_used_seconds": 0,
                    "approvals": [],
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["result"]["allowed"] is False
        assert body["data"]["result"]["reason"] == "DENIED_AGE_BELOW_MINIMUM"


@pytest.mark.asyncio
async def test_full_api_flow(app_fixture, sample_policy):
    app, clock = app_fixture
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Create policy.
        r = await c.post(
            "/api/v1/policies",
            headers={"X-Tenant-Id": "t1"},
            json={"document": sample_policy, "idempotency_key": "k1"},
        )
        assert r.status_code == 200, r.text
        pid = r.json()["data"]["policy_id"]

        # Start session.
        r = await c.post(
            "/api/v1/sessions/start",
            headers={"X-Tenant-Id": "t1"},
            json={
                "user_id": "u1",
                "policy_id": pid,
                "user_age": 20,
                "user_timezone": "UTC",
                "idempotency_key": "sk1",
            },
        )
        assert r.status_code == 200, r.text
        sid = r.json()["data"]["session_id"]

        # Heartbeat.
        clock.advance(60)
        r = await c.post(
            f"/api/v1/sessions/{sid}/heartbeat",
            headers={"X-Tenant-Id": "t1"},
            json={"sequence": 1},
        )
        assert r.status_code == 200
        assert r.json()["data"]["state"] == "ACTIVE"

        # Usage.
        r = await c.get(
            "/api/v1/usage",
            headers={"X-Tenant-Id": "t1"},
            params={"user_id": "u1", "user_timezone": "UTC"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["current_session_id"] == sid

        # Pause then end.
        r = await c.post(
            f"/api/v1/sessions/{sid}/pause", headers={"X-Tenant-Id": "t1"}, json={}
        )
        assert r.status_code == 200
        r = await c.post(
            f"/api/v1/sessions/{sid}/end", headers={"X-Tenant-Id": "t1"}, json={}
        )
        assert r.status_code == 200
        assert r.json()["data"]["state"] == "ENDED"

        # Replay.
        r = await c.post(
            f"/api/v1/sessions/{sid}/replay", headers={"X-Tenant-Id": "t1"}
        )
        assert r.status_code == 200
        steps = r.json()["data"]["steps"]
        assert any(s["event"] == "session.started" for s in steps)
