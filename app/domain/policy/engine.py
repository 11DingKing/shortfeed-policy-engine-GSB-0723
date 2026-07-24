from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone, date
from typing import Any

import pytz

from app.domain.enums import ReasonCode
from app.domain.policy.ast import (
    PolicyAST, PolicyRule, AgeGateRule, DailyLimitRule, SessionLimitRule,
    BedtimeBanRule, TimeWindowRule, ExceptionRule, AndRule, OrRule, NotRule,
)


@dataclass
class EvaluationContext:
    user_id: str
    user_age: int | None = None
    user_timezone: str = "UTC"
    current_time: datetime | None = None
    daily_used_minutes: int = 0
    session_active_seconds: int = 0
    has_active_exception: bool = False


@dataclass
class TraceNode:
    rule_type: str
    rule_description: str
    passed: bool
    reason_code: ReasonCode
    detail: dict[str, Any] = field(default_factory=dict)
    children: list[TraceNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "rule_description": self.rule_description,
            "passed": self.passed,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class EvaluationResult:
    allowed: bool
    reason_code: ReasonCode
    trace: TraceNode


def evaluate_policy(ast: PolicyAST, ctx: EvaluationContext) -> EvaluationResult:
    trace = _evaluate(ast.rule, ctx)
    allowed = trace.passed
    reason = trace.reason_code
    if allowed and ctx.has_active_exception:
        reason = ReasonCode.EXCEPTION_APPROVED
    return EvaluationResult(allowed=allowed, reason_code=reason, trace=trace)


def _evaluate(rule: PolicyRule, ctx: EvaluationContext) -> TraceNode:
    if isinstance(rule, AgeGateRule):
        return _eval_age_gate(rule, ctx)
    if isinstance(rule, DailyLimitRule):
        return _eval_daily_limit(rule, ctx)
    if isinstance(rule, SessionLimitRule):
        return _eval_session_limit(rule, ctx)
    if isinstance(rule, BedtimeBanRule):
        return _eval_bedtime_ban(rule, ctx)
    if isinstance(rule, TimeWindowRule):
        return _eval_time_window(rule, ctx)
    if isinstance(rule, ExceptionRule):
        return _eval_exception(rule, ctx)
    if isinstance(rule, AndRule):
        return _eval_and(rule, ctx)
    if isinstance(rule, OrRule):
        return _eval_or(rule, ctx)
    if isinstance(rule, NotRule):
        return _eval_not(rule, ctx)
    return TraceNode(
        rule_type="unknown",
        rule_description="Unknown rule type",
        passed=False,
        reason_code=ReasonCode.INVALID_POLICY_AST,
    )


def _now(ctx: EvaluationContext) -> datetime:
    return ctx.current_time or datetime.now(timezone.utc)


def _local_time(ctx: EvaluationContext, tz_name: str) -> tuple[datetime, dt_time, int]:
    tz = pytz.timezone(tz_name)
    now_utc = _now(ctx)
    local = now_utc.astimezone(tz)
    return local, local.time(), local.weekday()


def _eval_age_gate(rule: AgeGateRule, ctx: EvaluationContext) -> TraceNode:
    age = ctx.user_age
    if age is None:
        return TraceNode(
            rule_type="age_gate",
            rule_description=f"Minimum age: {rule.min_age}",
            passed=False,
            reason_code=ReasonCode.AGE_GATE_BLOCKED,
            detail={"min_age": rule.min_age, "user_age": None, "message": "Age not provided"},
        )
    passed = age >= rule.min_age
    return TraceNode(
        rule_type="age_gate",
        rule_description=f"Minimum age: {rule.min_age}",
        passed=passed,
        reason_code=ReasonCode.OK if passed else ReasonCode.AGE_GATE_BLOCKED,
        detail={"min_age": rule.min_age, "user_age": age},
    )


def _eval_daily_limit(rule: DailyLimitRule, ctx: EvaluationContext) -> TraceNode:
    used = ctx.daily_used_minutes
    limit = rule.max_minutes_per_day
    passed = used < limit
    return TraceNode(
        rule_type="daily_limit",
        rule_description=f"Daily limit: {limit} minutes (timezone: {rule.timezone})",
        passed=passed,
        reason_code=ReasonCode.OK if passed else ReasonCode.DAILY_LIMIT_EXCEEDED,
        detail={
            "max_minutes_per_day": limit,
            "used_minutes": used,
            "remaining_minutes": max(0, limit - used),
            "timezone": rule.timezone,
        },
    )


def _eval_session_limit(rule: SessionLimitRule, ctx: EvaluationContext) -> TraceNode:
    active_sec = ctx.session_active_seconds
    limit_sec = rule.max_minutes_per_session * 60
    passed = active_sec < limit_sec
    return TraceNode(
        rule_type="session_limit",
        rule_description=f"Session limit: {rule.max_minutes_per_session} minutes",
        passed=passed,
        reason_code=ReasonCode.OK if passed else ReasonCode.SESSION_LIMIT_EXCEEDED,
        detail={
            "max_minutes_per_session": rule.max_minutes_per_session,
            "active_seconds": active_sec,
            "active_minutes": round(active_sec / 60, 2),
            "remaining_seconds": max(0, limit_sec - active_sec),
        },
    )


def _is_time_in_range(start: dt_time, end: dt_time, current: dt_time) -> bool:
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _eval_bedtime_ban(rule: BedtimeBanRule, ctx: EvaluationContext) -> TraceNode:
    _, local_t, _ = _local_time(ctx, rule.timezone)
    in_bedtime = _is_time_in_range(rule.start_time, rule.end_time, local_t)
    passed = not in_bedtime
    return TraceNode(
        rule_type="bedtime_ban",
        rule_description=f"Bedtime ban: {rule.start_time.strftime('%H:%M')}-{rule.end_time.strftime('%H:%M')} ({rule.timezone})",
        passed=passed,
        reason_code=ReasonCode.OK if passed else ReasonCode.BEDTIME_BAN_ACTIVE,
        detail={
            "bedtime_start": rule.start_time.strftime("%H:%M"),
            "bedtime_end": rule.end_time.strftime("%H:%M"),
            "local_time": local_t.strftime("%H:%M"),
            "timezone": rule.timezone,
            "in_bedtime": in_bedtime,
        },
    )


def _eval_time_window(rule: TimeWindowRule, ctx: EvaluationContext) -> TraceNode:
    local_dt, local_t, weekday = _local_time(ctx, rule.timezone)
    in_window = _is_time_in_range(rule.start_time, rule.end_time, local_t)
    day_ok = True
    if rule.days_of_week is not None:
        day_ok = weekday in rule.days_of_week
    passed = in_window and day_ok
    reason = ReasonCode.OK if passed else ReasonCode.NOT_IN_TIME_WINDOW
    detail: dict[str, Any] = {
        "window_start": rule.start_time.strftime("%H:%M"),
        "window_end": rule.end_time.strftime("%H:%M"),
        "local_time": local_t.strftime("%H:%M"),
        "local_day_of_week": weekday,
        "timezone": rule.timezone,
        "in_window": in_window,
        "day_allowed": day_ok,
    }
    if rule.days_of_week is not None:
        detail["allowed_days"] = rule.days_of_week
    return TraceNode(
        rule_type="time_window",
        rule_description=f"Allowed window: {rule.start_time.strftime('%H:%M')}-{rule.end_time.strftime('%H:%M')} ({rule.timezone})",
        passed=passed,
        reason_code=reason,
        detail=detail,
    )


def _eval_exception(rule: ExceptionRule, ctx: EvaluationContext) -> TraceNode:
    is_exempt = ctx.user_id in rule.user_ids
    not_expired = True
    if rule.valid_until is not None:
        try:
            exp = datetime.fromisoformat(rule.valid_until.replace("Z", "+00:00"))
            not_expired = _now(ctx) <= exp
        except (ValueError, TypeError):
            not_expired = False
    passed = is_exempt and not_expired
    return TraceNode(
        rule_type="exception",
        rule_description=f"Exception approval: {rule.reason or 'no reason'}",
        passed=passed,
        reason_code=ReasonCode.EXCEPTION_APPROVED if passed else ReasonCode.OK,
        detail={
            "user_is_exempt": is_exempt,
            "not_expired": not_expired,
            "valid_until": rule.valid_until,
        },
    )


def _eval_and(rule: AndRule, ctx: EvaluationContext) -> TraceNode:
    children: list[TraceNode] = []
    all_passed = True
    fail_reason = ReasonCode.OK
    for child in rule.rules:
        t = _evaluate(child, ctx)
        children.append(t)
        if not t.passed:
            all_passed = False
            fail_reason = t.reason_code
    return TraceNode(
        rule_type="and",
        rule_description="All rules must pass",
        passed=all_passed,
        reason_code=fail_reason if not all_passed else ReasonCode.OK,
        children=children,
    )


def _eval_or(rule: OrRule, ctx: EvaluationContext) -> TraceNode:
    children: list[TraceNode] = []
    any_passed = False
    for child in rule.rules:
        t = _evaluate(child, ctx)
        children.append(t)
        if t.passed:
            any_passed = True
    if any_passed:
        for c in children:
            if c.passed and c.reason_code == ReasonCode.EXCEPTION_APPROVED:
                return TraceNode(
                    rule_type="or",
                    rule_description="At least one rule must pass",
                    passed=True,
                    reason_code=ReasonCode.EXCEPTION_APPROVED,
                    children=children,
                )
    return TraceNode(
        rule_type="or",
        rule_description="At least one rule must pass",
        passed=any_passed,
        reason_code=ReasonCode.OK if any_passed else (children[-1].reason_code if children else ReasonCode.OK),
        children=children,
    )


def _eval_not(rule: NotRule, ctx: EvaluationContext) -> TraceNode:
    child = _evaluate(rule.rule, ctx)
    return TraceNode(
        rule_type="not",
        rule_description="Negation of sub-rule",
        passed=not child.passed,
        reason_code=ReasonCode.OK if not child.passed else ReasonCode.OK,
        children=[child],
        detail={"negated": child.passed},
    )


def replay_evaluation(ast: PolicyAST, ctx: EvaluationContext) -> EvaluationResult:
    return evaluate_policy(ast, ctx)
