from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.enums import ReasonCode, SessionStatus
from app.domain.session.aggregate import SessionAggregate


NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _make_session(**kwargs) -> SessionAggregate:
    defaults = dict(
        session_id="sess-1",
        tenant_id="tenant-1",
        user_id="user-1",
        policy_id="pol-1",
        policy_version=1,
        now=NOW,
        user_age=25,
        user_timezone="UTC",
        idempotency_key=None,
    )
    defaults.update(kwargs)
    return SessionAggregate.create(**defaults)


class TestSessionLifecycle:
    def test_create_active(self):
        s = _make_session()
        assert s.status == SessionStatus.ACTIVE
        assert s.total_active_seconds == 0
        assert s.last_heartbeat_seq == 0

    def test_heartbeat_accumulates_time(self):
        s = _make_session()
        ok, rc = s.heartbeat(1, NOW + timedelta(seconds=60))
        assert ok is True
        assert rc == ReasonCode.OK
        assert s.total_active_seconds == 60
        assert s.last_heartbeat_seq == 1

    def test_duplicate_heartbeat(self):
        s = _make_session()
        s.heartbeat(1, NOW + timedelta(seconds=60))
        ok, rc = s.heartbeat(1, NOW + timedelta(seconds=120))
        assert ok is False
        assert rc == ReasonCode.DUPLICATE_HEARTBEAT
        assert s.total_active_seconds == 60

    def test_out_of_order_heartbeat(self):
        s = _make_session()
        s.heartbeat(5, NOW + timedelta(seconds=60))
        ok, rc = s.heartbeat(3, NOW + timedelta(seconds=120))
        assert ok is False
        assert rc == ReasonCode.HEARTBEAT_OUT_OF_ORDER

    def test_pause_and_resume(self):
        s = _make_session()
        s.heartbeat(1, NOW + timedelta(seconds=120))
        ok, rc = s.pause(NOW + timedelta(seconds=120))
        assert ok is True
        assert s.status == SessionStatus.PAUSED
        assert s.total_active_seconds == 120

        ok, rc = s.heartbeat(2, NOW + timedelta(seconds=300))
        assert ok is False
        assert rc == ReasonCode.SESSION_NOT_ACTIVE

        ok, rc = s.resume(NOW + timedelta(seconds=300))
        assert ok is True
        assert s.status == SessionStatus.ACTIVE

        ok, rc = s.heartbeat(2, NOW + timedelta(seconds=360))
        assert ok is True
        assert s.total_active_seconds == 180

    def test_double_pause(self):
        s = _make_session()
        s.pause(NOW)
        ok, rc = s.pause(NOW + timedelta(seconds=10))
        assert ok is False
        assert rc == ReasonCode.SESSION_ALREADY_PAUSED

    def test_resume_when_not_paused(self):
        s = _make_session()
        ok, rc = s.resume(NOW)
        assert ok is False
        assert rc == ReasonCode.SESSION_NOT_PAUSED

    def test_end(self):
        s = _make_session()
        s.heartbeat(1, NOW + timedelta(seconds=300))
        ok, rc = s.end(NOW + timedelta(seconds=300))
        assert ok is True
        assert s.status == SessionStatus.ENDED
        assert s.ended_at is not None
        assert s.total_active_seconds == 300

    def test_double_end(self):
        s = _make_session()
        s.end(NOW)
        ok, rc = s.end(NOW + timedelta(seconds=10))
        assert ok is False
        assert rc == ReasonCode.SESSION_ALREADY_ENDED

    def test_heartbeat_after_end(self):
        s = _make_session()
        s.end(NOW)
        ok, rc = s.heartbeat(1, NOW + timedelta(seconds=60))
        assert ok is False
        assert rc == ReasonCode.SESSION_ALREADY_ENDED

    def test_evaluation_force_expires(self):
        s = _make_session()
        s.heartbeat(1, NOW + timedelta(seconds=600))
        rc = s.apply_evaluation_result(
            False, ReasonCode.SESSION_LIMIT_EXCEEDED,
            {"detail": "limit reached"}, NOW + timedelta(seconds=600),
        )
        assert rc == ReasonCode.SESSION_LIMIT_EXCEEDED
        assert s.status == SessionStatus.EXPIRED
        assert s.ended_at is not None

    def test_events_recorded(self):
        s = _make_session()
        s.heartbeat(1, NOW + timedelta(seconds=60))
        s.pause(NOW + timedelta(seconds=60))
        s.resume(NOW + timedelta(seconds=120))
        s.end(NOW + timedelta(seconds=180))
        events = s.drain_events()
        actions = [e.action.value for e in events]
        assert "start" in actions
        assert "heartbeat" in actions
        assert "pause" in actions
        assert "resume" in actions
        assert "end" in actions
