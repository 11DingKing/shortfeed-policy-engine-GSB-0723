"""Tests for the policy AST parser and interpreter."""
from __future__ import annotations

from datetime import UTC, datetime

from spe.domain.policy_ast import EvaluationContext, parse_policy
from spe.domain.policy_interpreter import evaluate
from spe.domain.reason_codes import ReasonCode


def _ctx(**kw) -> EvaluationContext:
    defaults = dict(
        user_age=15,
        user_timezone="Asia/Shanghai",
        now_utc=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        daily_used_seconds=0,
        session_used_seconds=0,
        approvals=set(),
    )
    defaults.update(kw)
    return EvaluationContext(**defaults)


def test_adult_allowed(sample_policy):
    doc = parse_policy(sample_policy)
    r = evaluate(doc, _ctx(user_age=25, user_timezone="Asia/Shanghai"))
    assert r.allowed is True
    assert r.reason == ReasonCode.ALLOWED.value


def test_under_13_denied(sample_policy):
    doc = parse_policy(sample_policy)
    r = evaluate(doc, _ctx(user_age=10))
    assert r.allowed is False
    assert r.reason == ReasonCode.DENIED_AGE_BELOW_MINIMUM.value
    assert r.rule_id == "age-under-13"


def test_teen_session_cap(sample_policy):
    doc = parse_policy(sample_policy)
    # 15 minutes in, teen should hit session cap.
    r = evaluate(doc, _ctx(user_age=15, session_used_seconds=900))
    assert r.allowed is False
    assert r.reason == ReasonCode.LIMITED_SESSION_QUOTA_REACHED.value
    assert r.rule_id == "teen-session-cap"


def test_teen_daily_cap(sample_policy):
    doc = parse_policy(sample_policy)
    r = evaluate(doc, _ctx(user_age=15, daily_used_seconds=3600))
    assert r.allowed is False
    assert r.reason == ReasonCode.LIMITED_DAILY_QUOTA_REACHED.value


def test_adult_daily_cap(sample_policy):
    doc = parse_policy(sample_policy)
    r = evaluate(doc, _ctx(user_age=25, daily_used_seconds=7200))
    assert r.allowed is False
    assert r.reason == ReasonCode.LIMITED_DAILY_QUOTA_REACHED.value


def test_bedtime_denied_when_in_window(sample_policy):
    # Asia/Shanghai UTC+8; 20:00 UTC = 04:00 local (inside 22:00-06:00)
    doc = parse_policy(sample_policy)
    now = datetime(2026, 6, 1, 20, 0, 0, tzinfo=UTC)
    r = evaluate(doc, _ctx(user_age=15, now_utc=now))
    assert r.allowed is False
    assert r.reason == ReasonCode.DENIED_BEDTIME_WINDOW.value


def test_bedtime_override_with_approval(sample_policy):
    doc = parse_policy(sample_policy)
    now = datetime(2026, 6, 1, 20, 0, 0, tzinfo=UTC)
    r = evaluate(doc, _ctx(user_age=15, now_utc=now, approvals={"parental-exception"}))
    assert r.allowed is True


def test_trace_is_deterministic(sample_policy):
    doc = parse_policy(sample_policy)
    ctx = _ctx(user_age=15, session_used_seconds=900)
    r1 = evaluate(doc, ctx)
    r2 = evaluate(doc, ctx)
    assert [t.model_dump() for t in r1.trace] == [t.model_dump() for t in r2.trace]


def test_unknown_timezone_rejected():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _ctx(user_timezone="Not/AZone")
