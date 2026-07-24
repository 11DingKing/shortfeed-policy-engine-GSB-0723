"""Tests for the pure session state machine."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from spe.domain.reason_codes import ReasonCode
from spe.domain.session import SessionAggregate, SessionState


def _make():
    now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    return SessionAggregate.create(
        id="s1",
        tenant_id="t1",
        user_id="u1",
        policy_id="p1",
        policy_version="1",
        policy_document={"name": "x", "rules": []},
        now=now,
        user_age=15,
        user_timezone="Asia/Shanghai",
    ), now


def test_start_is_active():
    s, _ = _make()
    assert s.state is SessionState.ACTIVE


def test_heartbeat_accumulates():
    s, now = _make()
    s.apply_heartbeat(now + timedelta(seconds=30), seq=1)
    assert abs(s.accumulated_active_seconds - 30) < 1e-6
    s.apply_heartbeat(now + timedelta(seconds=60), seq=2)
    assert abs(s.accumulated_active_seconds - 60) < 1e-6


def test_duplicate_heartbeat_ignored():
    s, now = _make()
    s.apply_heartbeat(now + timedelta(seconds=10), seq=1)
    d = s.apply_heartbeat(now + timedelta(seconds=20), seq=1)
    assert not d.accepted
    assert d.reason is ReasonCode.HEARTBEAT_DUPLICATE
    assert abs(s.accumulated_active_seconds - 10) < 1e-6


def test_out_of_order_heartbeat_ignored():
    s, now = _make()
    s.apply_heartbeat(now + timedelta(seconds=10), seq=5)
    d = s.apply_heartbeat(now + timedelta(seconds=20), seq=3)
    assert not d.accepted
    assert d.reason is ReasonCode.HEARTBEAT_OUT_OF_ORDER


def test_pause_blocks_heartbeat():
    s, now = _make()
    s.apply_pause(now + timedelta(seconds=10))
    d = s.apply_heartbeat(now + timedelta(seconds=20), seq=2)
    assert not d.accepted
    assert d.reason is ReasonCode.HEARTBEAT_PROHIBITED_WHEN_PAUSED


def test_pause_resume_cycle():
    s, now = _make()
    s.apply_pause(now + timedelta(seconds=10))
    assert s.state is SessionState.PAUSED
    s.apply_resume(now + timedelta(seconds=20))
    assert s.state is SessionState.ACTIVE
    s.apply_heartbeat(now + timedelta(seconds=30), seq=1)
    assert abs(s.accumulated_active_seconds - 20) < 1e-6  # 10s before pause + 10s after resume


def test_end_is_idempotent():
    s, now = _make()
    s.apply_end(now + timedelta(seconds=5))
    assert s.state is SessionState.ENDED
    d = s.apply_end(now + timedelta(seconds=99))
    assert d.reason is ReasonCode.SESSION_ALREADY_ENDED


def test_resume_when_active_rejected():
    s, now = _make()
    d = s.apply_resume(now)
    assert not d.accepted
    assert d.reason is ReasonCode.SESSION_NOT_PAUSED
