from __future__ import annotations

from datetime import datetime, timezone, time as dt_time

import pytest
import pytz

from app.domain.enums import ReasonCode
from app.domain.policy.ast import (
    PolicyAST, AgeGateRule, DailyLimitRule, SessionLimitRule,
    BedtimeBanRule, TimeWindowRule, ExceptionRule, AndRule, OrRule, NotRule,
)
from app.domain.policy.checker import validate_policy
from app.domain.policy.engine import EvaluationContext, evaluate_policy, replay_evaluation


def _make_ast(rule, name="test", version=1):
    return PolicyAST(version=version, name=name, rule=rule)


def _ctx(**kwargs):
    defaults = dict(
        user_id="user-1",
        user_age=25,
        user_timezone="UTC",
        current_time=datetime(2026, 7, 24, 14, 0, 0, tzinfo=timezone.utc),
        daily_used_minutes=0,
        session_active_seconds=0,
    )
    defaults.update(kwargs)
    return EvaluationContext(**defaults)


class TestAgeGate:
    def test_age_ok(self):
        ast = _make_ast(AgeGateRule(min_age=18))
        result = evaluate_policy(ast, _ctx(user_age=25))
        assert result.allowed is True
        assert result.reason_code == ReasonCode.OK

    def test_age_blocked(self):
        ast = _make_ast(AgeGateRule(min_age=18))
        result = evaluate_policy(ast, _ctx(user_age=15))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.AGE_GATE_BLOCKED

    def test_age_none_blocked(self):
        ast = _make_ast(AgeGateRule(min_age=18))
        result = evaluate_policy(ast, _ctx(user_age=None))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.AGE_GATE_BLOCKED


class TestDailyLimit:
    def test_under_limit(self):
        ast = _make_ast(DailyLimitRule(max_minutes_per_day=120))
        result = evaluate_policy(ast, _ctx(daily_used_minutes=60))
        assert result.allowed is True

    def test_at_limit_blocks(self):
        ast = _make_ast(DailyLimitRule(max_minutes_per_day=60))
        result = evaluate_policy(ast, _ctx(daily_used_minutes=60))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.DAILY_LIMIT_EXCEEDED

    def test_over_limit_blocks(self):
        ast = _make_ast(DailyLimitRule(max_minutes_per_day=60))
        result = evaluate_policy(ast, _ctx(daily_used_minutes=61))
        assert result.allowed is False


class TestSessionLimit:
    def test_under_limit(self):
        ast = _make_ast(SessionLimitRule(max_minutes_per_session=30))
        result = evaluate_policy(ast, _ctx(session_active_seconds=600))
        assert result.allowed is True

    def test_at_limit_blocks(self):
        ast = _make_ast(SessionLimitRule(max_minutes_per_session=10))
        result = evaluate_policy(ast, _ctx(session_active_seconds=600))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.SESSION_LIMIT_EXCEEDED


class TestBedtimeBan:
    def test_outside_bedtime(self):
        ast = _make_ast(BedtimeBanRule(
            start_time=dt_time(22, 0), end_time=dt_time(6, 0), timezone="UTC",
        ))
        result = evaluate_policy(ast, _ctx(
            current_time=datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        ))
        assert result.allowed is True

    def test_during_bedtime_overnight(self):
        ast = _make_ast(BedtimeBanRule(
            start_time=dt_time(22, 0), end_time=dt_time(6, 0), timezone="UTC",
        ))
        result = evaluate_policy(ast, _ctx(
            current_time=datetime(2026, 7, 24, 23, 0, tzinfo=timezone.utc)
        ))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.BEDTIME_BAN_ACTIVE

    def test_during_bedtime_morning(self):
        ast = _make_ast(BedtimeBanRule(
            start_time=dt_time(22, 0), end_time=dt_time(6, 0), timezone="UTC",
        ))
        result = evaluate_policy(ast, _ctx(
            current_time=datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc)
        ))
        assert result.allowed is False


class TestTimeWindow:
    def test_in_window(self):
        ast = _make_ast(TimeWindowRule(
            start_time=dt_time(8, 0), end_time=dt_time(20, 0), timezone="UTC",
        ))
        result = evaluate_policy(ast, _ctx(
            current_time=datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        ))
        assert result.allowed is True

    def test_outside_window(self):
        ast = _make_ast(TimeWindowRule(
            start_time=dt_time(8, 0), end_time=dt_time(20, 0), timezone="UTC",
        ))
        result = evaluate_policy(ast, _ctx(
            current_time=datetime(2026, 7, 24, 22, 0, tzinfo=timezone.utc)
        ))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.NOT_IN_TIME_WINDOW


class TestException:
    def test_exception_passes(self):
        ast = _make_ast(ExceptionRule(
            user_ids=["user-1", "user-2"], reason="parental approval",
        ))
        result = evaluate_policy(ast, _ctx(user_id="user-1"))
        assert result.allowed is True
        assert result.reason_code == ReasonCode.EXCEPTION_APPROVED

    def test_non_exempt_user(self):
        ast = _make_ast(ExceptionRule(user_ids=["user-2"]))
        result = evaluate_policy(ast, _ctx(user_id="user-1"))
        assert result.allowed is False


class TestLogicalCombinators:
    def test_and_all_pass(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=18),
            DailyLimitRule(max_minutes_per_day=120),
        ]))
        result = evaluate_policy(ast, _ctx(user_age=25, daily_used_minutes=60))
        assert result.allowed is True

    def test_and_one_fails(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=18),
            DailyLimitRule(max_minutes_per_day=60),
        ]))
        result = evaluate_policy(ast, _ctx(user_age=25, daily_used_minutes=61))
        assert result.allowed is False
        assert result.reason_code == ReasonCode.DAILY_LIMIT_EXCEEDED

    def test_or_one_passes(self):
        ast = _make_ast(OrRule(rules=[
            AgeGateRule(min_age=18),
            ExceptionRule(user_ids=["user-1"]),
        ]))
        result = evaluate_policy(ast, _ctx(user_age=15, user_id="user-1"))
        assert result.allowed is True

    def test_or_none_pass(self):
        ast = _make_ast(OrRule(rules=[
            AgeGateRule(min_age=18),
            ExceptionRule(user_ids=["user-2"]),
        ]))
        result = evaluate_policy(ast, _ctx(user_age=15, user_id="user-1"))
        assert result.allowed is False

    def test_not_negates(self):
        ast = _make_ast(NotRule(rule=AgeGateRule(min_age=18)))
        result = evaluate_policy(ast, _ctx(user_age=15))
        assert result.allowed is True


class TestComplexPolicy:
    def test_full_policy(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=13),
            OrRule(rules=[
                ExceptionRule(user_ids=["vip-user"]),
                AndRule(rules=[
                    DailyLimitRule(max_minutes_per_day=120, timezone="Asia/Shanghai"),
                    SessionLimitRule(max_minutes_per_session=30),
                    NotRule(rule=BedtimeBanRule(
                        start_time=dt_time(22, 0), end_time=dt_time(6, 0),
                        timezone="Asia/Shanghai",
                    )),
                ]),
            ]),
        ]))
        result = evaluate_policy(ast, _ctx(
            user_age=15, user_id="normal-user",
            daily_used_minutes=30, session_active_seconds=600,
            current_time=datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc),
        ))
        assert result.allowed is True

    def test_vip_bypasses_limits(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=13),
            OrRule(rules=[
                ExceptionRule(user_ids=["vip-user"]),
                DailyLimitRule(max_minutes_per_day=30),
            ]),
        ]))
        result = evaluate_policy(ast, _ctx(
            user_id="vip-user", user_age=25, daily_used_minutes=200,
        ))
        assert result.allowed is True


class TestValidation:
    def test_valid_policy(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=18),
            DailyLimitRule(max_minutes_per_day=120, timezone="UTC"),
        ]))
        errors = validate_policy(ast)
        assert len(errors) == 0

    def test_invalid_timezone(self):
        ast = _make_ast(DailyLimitRule(max_minutes_per_day=120, timezone="Invalid/Zone"))
        errors = validate_policy(ast)
        assert len(errors) > 0

    def test_bedtime_same_time(self):
        ast = _make_ast(BedtimeBanRule(
            start_time=dt_time(22, 0), end_time=dt_time(22, 0), timezone="UTC",
        ))
        errors = validate_policy(ast)
        assert len(errors) > 0


class TestExplanationTrace:
    def test_trace_structure(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=18),
            DailyLimitRule(max_minutes_per_day=120),
        ]))
        result = evaluate_policy(ast, _ctx(user_age=25, daily_used_minutes=60))
        trace = result.trace.to_dict()
        assert trace["rule_type"] == "and"
        assert trace["passed"] is True
        assert len(trace["children"]) == 2
        assert trace["children"][0]["rule_type"] == "age_gate"
        assert trace["children"][1]["rule_type"] == "daily_limit"

    def test_trace_shows_failure_reason(self):
        ast = _make_ast(AndRule(rules=[
            AgeGateRule(min_age=18),
            DailyLimitRule(max_minutes_per_day=60),
        ]))
        result = evaluate_policy(ast, _ctx(user_age=25, daily_used_minutes=61))
        trace = result.trace.to_dict()
        assert trace["passed"] is False
        assert trace["reason_code"] == "daily_limit_exceeded"


class TestReplay:
    def test_replay_deterministic(self):
        ast = _make_ast(AgeGateRule(min_age=18))
        ctx = _ctx(user_age=25)
        r1 = evaluate_policy(ast, ctx)
        r2 = replay_evaluation(ast, ctx)
        assert r1.allowed == r2.allowed
        assert r1.reason_code == r2.reason_code

    def test_timezone_daily_limit(self):
        tz = pytz.timezone("Asia/Shanghai")
        ast = _make_ast(DailyLimitRule(max_minutes_per_day=120, timezone="Asia/Shanghai"))
        shanghai_2pm = datetime(2026, 7, 24, 14, 0, tzinfo=tz)
        result = evaluate_policy(ast, _ctx(
            daily_used_minutes=60,
            current_time=shanghai_2pm,
            user_timezone="Asia/Shanghai",
        ))
        assert result.allowed is True
