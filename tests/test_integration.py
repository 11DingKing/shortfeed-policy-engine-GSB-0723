from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytz
from sqlalchemy import text

from tests.conftest import requires_postgres
from app.domain.enums import ReasonCode, SessionStatus
from app.domain.policy.ast import (
    PolicyAST, AgeGateRule, DailyLimitRule, SessionLimitRule,
    BedtimeBanRule, AndRule, NotRule,
)
from app.services.policy_service import PolicyService
from app.services.session_service import SessionService
from app.infrastructure.clock import FixedClock
from app.infrastructure.id_generator import SequentialGenerator


TENANT = "default"


def _make_policy_simple() -> PolicyAST:
    return PolicyAST(
        version=1,
        name="simple",
        rule=AndRule(rules=[
            AgeGateRule(min_age=13),
            DailyLimitRule(max_minutes_per_day=120, timezone="UTC"),
            SessionLimitRule(max_minutes_per_session=30),
        ]),
    )


async def _publish_policy(db_session, svc: PolicyService, ast: PolicyAST):
    result, rc, _ = await svc.publish_policy(TENANT, "test-policy", ast)
    assert rc == ReasonCode.OK
    return result


@requires_postgres
async def test_full_session_lifecycle(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, msg = await session_svc.start_session(
        TENANT, "user-1", user_age=20, user_timezone="UTC",
    )
    assert rc == ReasonCode.OK
    assert result is not None
    assert result["status"] == "active"
    session_id = result["id"]
    await db_session.commit()

    clock.advance(seconds=60)
    result, rc, msg = await session_svc.heartbeat(TENANT, session_id, 1)
    assert rc == ReasonCode.OK, f"Expected OK, got {rc}: {msg}"
    assert result["total_active_seconds"] == 60
    await db_session.commit()

    clock.advance(seconds=120)
    result, rc, msg = await session_svc.heartbeat(TENANT, session_id, 2)
    assert rc == ReasonCode.OK
    assert result["total_active_seconds"] == 180
    await db_session.commit()

    usage = await session_svc.query_usage(TENANT, "user-1", "UTC")
    assert usage["total_active_seconds"] == 180
    assert usage["total_sessions"] == 1
    await db_session.commit()

    result, rc, msg = await session_svc.end_session(TENANT, session_id)
    assert rc == ReasonCode.OK
    assert result["status"] == "ended"
    await db_session.commit()


@requires_postgres
async def test_duplicate_heartbeat_handled_correctly(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-hb", user_age=20)
    session_id = result["id"]
    assert rc == ReasonCode.OK
    await db_session.commit()

    clock.advance(seconds=60)
    result, rc, _ = await session_svc.heartbeat(TENANT, session_id, 1)
    assert rc == ReasonCode.OK
    assert result["total_active_seconds"] == 60
    await db_session.commit()

    result2, rc2, _ = await session_svc.heartbeat(TENANT, session_id, 1)
    assert rc2 == ReasonCode.DUPLICATE_HEARTBEAT
    assert result2["total_active_seconds"] == 60
    await db_session.commit()

    clock.advance(seconds=30)
    result3, rc3, _ = await session_svc.heartbeat(TENANT, session_id, 2)
    assert rc3 == ReasonCode.OK
    assert result3["total_active_seconds"] == 90
    await db_session.commit()


@requires_postgres
async def test_out_of_order_heartbeat_rejected(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-ooo", user_age=20)
    session_id = result["id"]
    await db_session.commit()

    clock.advance(seconds=60)
    await session_svc.heartbeat(TENANT, session_id, 5)
    await db_session.commit()

    _, rc_ooo, _ = await session_svc.heartbeat(TENANT, session_id, 3)
    assert rc_ooo == ReasonCode.HEARTBEAT_OUT_OF_ORDER
    await db_session.commit()


@requires_postgres
async def test_concurrent_session_prevented_by_db_constraint(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result1, rc1, _ = await session_svc.start_session(TENANT, "user-concurrent", user_age=20)
    assert rc1 == ReasonCode.OK
    await db_session.commit()

    result2, rc2, _ = await session_svc.start_session(TENANT, "user-concurrent", user_age=20)
    assert rc2 in (ReasonCode.SESSION_ALREADY_ACTIVE, ReasonCode.CONCURRENT_SESSION_BLOCKED)
    await db_session.commit()


@requires_postgres
async def test_pause_resume_lifecycle(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-pause", user_age=20)
    session_id = result["id"]
    await db_session.commit()

    clock.advance(seconds=120)
    await session_svc.heartbeat(TENANT, session_id, 1)
    await db_session.commit()

    clock.advance(seconds=60)
    result, rc, _ = await session_svc.pause(TENANT, session_id)
    assert rc == ReasonCode.OK
    assert result["status"] == "paused"
    assert result["total_active_seconds"] == 180
    await db_session.commit()

    _, rc_hb, _ = await session_svc.heartbeat(TENANT, session_id, 2)
    assert rc_hb == ReasonCode.SESSION_NOT_ACTIVE
    await db_session.commit()

    clock.advance(seconds=300)
    result, rc, _ = await session_svc.resume(TENANT, session_id)
    assert rc == ReasonCode.OK
    assert result["status"] == "active"
    await db_session.commit()

    clock.advance(seconds=60)
    result, rc, _ = await session_svc.heartbeat(TENANT, session_id, 2)
    assert rc == ReasonCode.OK
    assert result["total_active_seconds"] == 240
    await db_session.commit()


@requires_postgres
async def test_session_limit_enforced(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy = PolicyAST(
        version=1,
        name="short-session",
        rule=AndRule(rules=[
            AgeGateRule(min_age=13),
            SessionLimitRule(max_minutes_per_session=1),
        ]),
    )
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, policy)
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-limit", user_age=20)
    session_id = result["id"]
    assert rc == ReasonCode.OK
    await db_session.commit()

    clock.advance(seconds=61)
    result, rc, _ = await session_svc.heartbeat(TENANT, session_id, 1)
    await db_session.commit()

    sess = await session_svc.get_session(TENANT, session_id)
    assert sess["status"] in ("expired", "ended")


@requires_postgres
async def test_policy_version_pinned_to_session(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()

    policy_v1 = PolicyAST(version=1, name="v1", rule=AgeGateRule(min_age=18))
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    pub1 = await _publish_policy(db_session, policy_svc, policy_v1)
    assert pub1["version"] == 1
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(
        TENANT, "user-version", user_age=20, policy_version=1,
    )
    assert rc == ReasonCode.OK
    assert result["policy_version"] == 1
    session_id = result["id"]
    await db_session.commit()

    policy_v2 = PolicyAST(version=1, name="v2", rule=AgeGateRule(min_age=25))
    await _publish_policy(db_session, policy_svc, policy_v2)
    await db_session.commit()

    sess = await session_svc.get_session(TENANT, session_id)
    assert sess["policy_version"] == 1


@requires_postgres
async def test_cross_midnight_daily_usage_split(db_session):
    tz_name = "Asia/Shanghai"
    clock = FixedClock(datetime(2026, 7, 24, 15, 55, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()

    policy = PolicyAST(
        version=1, name="cross-midnight",
        rule=AndRule(rules=[
            AgeGateRule(min_age=13),
            DailyLimitRule(max_minutes_per_day=600, timezone=tz_name),
        ]),
    )
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, policy)
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(
        TENANT, "user-midnight", user_age=20, user_timezone=tz_name,
    )
    session_id = result["id"]
    assert rc == ReasonCode.OK
    await db_session.commit()

    clock.advance(minutes=10)
    result, rc, _ = await session_svc.heartbeat(TENANT, session_id, 1)
    assert rc == ReasonCode.OK
    await db_session.commit()

    from sqlalchemy import text
    r = await db_session.execute(
        text("SELECT usage_date, total_seconds FROM daily_usage WHERE user_id='user-midnight' ORDER BY usage_date")
    )
    rows = r.fetchall()
    date_seconds = {row[0]: row[1] for row in rows}
    assert len(date_seconds) == 2, f"Expected 2 dates, got {len(date_seconds)}: {date_seconds}"
    jul24 = datetime(2026, 7, 24).date()
    jul25 = datetime(2026, 7, 25).date()
    assert date_seconds[jul24] == 300, f"July 24 should have 300s, got {date_seconds[jul24]}"
    assert date_seconds[jul25] == 300, f"July 25 should have 300s, got {date_seconds[jul25]}"

    clock.advance(minutes=10)
    result, rc, _ = await session_svc.heartbeat(TENANT, session_id, 2)
    assert rc == ReasonCode.OK
    await db_session.commit()

    r2 = await db_session.execute(
        text("SELECT usage_date, total_seconds FROM daily_usage WHERE user_id='user-midnight' ORDER BY usage_date")
    )
    rows2 = r2.fetchall()
    date_seconds2 = {row[0]: row[1] for row in rows2}
    assert date_seconds2[jul24] == 300
    assert date_seconds2[jul25] == 900


@requires_postgres
async def test_idempotent_session_start(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result1, rc1, _ = await session_svc.start_session(
        TENANT, "user-idem", user_age=20, idempotency_key="idem-1",
    )
    assert rc1 == ReasonCode.OK
    sid1 = result1["id"]
    await db_session.commit()

    result2, rc2, _ = await session_svc.start_session(
        TENANT, "user-idem", user_age=20, idempotency_key="idem-1",
    )
    assert rc2 == ReasonCode.OK
    assert result2["id"] == sid1
    await db_session.commit()


@requires_postgres
async def test_history_replay(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-replay", user_age=20)
    session_id = result["id"]
    await db_session.commit()

    for i in range(1, 4):
        clock.advance(seconds=60)
        await session_svc.heartbeat(TENANT, session_id, i)
        await db_session.commit()

    replay = await session_svc.replay_session(TENANT, session_id)
    assert replay is not None
    assert len(replay) >= 4
    actions = [e["action"] for e in replay]
    assert "start" in actions
    assert actions.count("heartbeat") == 3


@requires_postgres
async def test_outbox_events_written(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-outbox", user_age=20)
    session_id = result["id"]
    await db_session.commit()

    clock.advance(seconds=60)
    await session_svc.heartbeat(TENANT, session_id, 1)
    await db_session.commit()

    await session_svc.end_session(TENANT, session_id)
    await db_session.commit()

    from sqlalchemy import text
    r = await db_session.execute(
        text("SELECT event_type FROM outbox_messages WHERE aggregate_id = :sid ORDER BY created_at"),
        {"sid": session_id},
    )
    events = [row[0] for row in r.fetchall()]
    assert "session.started" in events
    assert "session.ended" in events


@requires_postgres
async def test_tenant_isolation(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())

    from sqlalchemy import text
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES ('tenant-b', 'Tenant B') ON CONFLICT DO NOTHING")
    )
    await db_session.commit()

    ast = _make_policy_simple()
    await policy_svc.publish_policy("tenant-b", "policy-b", ast)
    await db_session.commit()

    result_a, _, _ = await session_svc.start_session(TENANT, "user-x", user_age=20)
    result_b, _, _ = await session_svc.start_session("tenant-b", "user-x", user_age=20)
    await db_session.commit()

    assert result_a["id"] != result_b["id"]
    assert result_a["tenant_id"] == TENANT
    assert result_b["tenant_id"] == "tenant-b"

    not_found = await session_svc.get_session("tenant-b", result_a["id"])
    assert not_found is None

    found = await session_svc.get_session(TENANT, result_a["id"])
    assert found is not None
    assert found["id"] == result_a["id"]


@requires_postgres
async def test_outbox_worker_processes_messages(db_session, session_factory):
    from app.worker import process_batch, OutboxPublisher

    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-outbox2", user_age=20)
    await db_session.commit()

    from sqlalchemy import text
    async with session_factory() as check_db:
        r = await check_db.execute(
            text("SELECT COUNT(*) FROM outbox_messages WHERE processed = false")
        )
        unprocessed_before = r.scalar()
    assert unprocessed_before >= 1

    publisher = OutboxPublisher()
    processed = await process_batch(publisher, session_factory=session_factory)
    assert processed >= 1

    async with session_factory() as check_db:
        r2 = await check_db.execute(
            text("SELECT COUNT(*) FROM outbox_messages WHERE processed = false")
        )
        unprocessed_after = r2.scalar()
    assert unprocessed_after == 0


@requires_postgres
async def test_restart_recovery_from_db(db_session):
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, _make_policy_simple())
    await db_session.commit()

    result, rc, _ = await session_svc.start_session(TENANT, "user-restart", user_age=20)
    session_id = result["id"]
    await db_session.commit()

    clock.advance(seconds=120)
    await session_svc.heartbeat(TENANT, session_id, 1)
    await db_session.commit()

    await session_svc.pause(TENANT, session_id)
    await db_session.commit()

    recovered = await session_svc.get_session(TENANT, session_id)
    assert recovered is not None
    assert recovered["status"] == "paused"
    assert recovered["total_active_seconds"] == 120
    assert recovered["id"] == session_id


@requires_postgres
async def test_multiple_sessions_accumulate_daily_usage(db_session):
    tz_name = "UTC"
    clock = FixedClock(datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc))
    id_gen = SequentialGenerator()
    policy = PolicyAST(
        version=1, name="daily-test",
        rule=AndRule(rules=[
            AgeGateRule(min_age=13),
            DailyLimitRule(max_minutes_per_day=10, timezone=tz_name),
        ]),
    )
    policy_svc = PolicyService(db_session, clock=clock, id_generator=id_gen)
    session_svc = SessionService(db_session, clock=clock, id_generator=id_gen)

    await _publish_policy(db_session, policy_svc, policy)
    await db_session.commit()

    r1, rc1, _ = await session_svc.start_session(TENANT, "user-multi", user_age=20, user_timezone=tz_name)
    sid1 = r1["id"]
    assert rc1 == ReasonCode.OK
    await db_session.commit()

    clock.advance(seconds=300)
    await session_svc.heartbeat(TENANT, sid1, 1)
    await session_svc.end_session(TENANT, sid1)
    await db_session.commit()

    clock.advance(seconds=60)
    r2, rc2, _ = await session_svc.start_session(TENANT, "user-multi", user_age=20, user_timezone=tz_name)
    sid2 = r2["id"]
    assert rc2 == ReasonCode.OK
    await db_session.commit()

    clock.advance(seconds=300)
    r3, rc3, _ = await session_svc.heartbeat(TENANT, sid2, 1)
    await db_session.commit()

    usage = await session_svc.query_usage(TENANT, "user-multi", tz_name)
    assert usage["total_active_seconds"] == 600
