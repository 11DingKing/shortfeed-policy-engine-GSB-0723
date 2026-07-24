from __future__ import annotations

import pytz
from datetime import time as dt_time

from app.domain.policy.ast import (
    PolicyAST, PolicyRule, AgeGateRule, DailyLimitRule, SessionLimitRule,
    BedtimeBanRule, TimeWindowRule, ExceptionRule, AndRule, OrRule, NotRule,
)


class PolicyValidationError:
    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def validate_policy(ast: PolicyAST) -> list[PolicyValidationError]:
    errors: list[PolicyValidationError] = []
    _validate_rule(ast.rule, "$.rule", errors)
    return errors


def _validate_rule(rule: PolicyRule, path: str, errors: list[PolicyValidationError]) -> None:
    if isinstance(rule, AgeGateRule):
        if rule.min_age < 0 or rule.min_age > 200:
            errors.append(PolicyValidationError(path, f"min_age must be 0-200, got {rule.min_age}"))

    elif isinstance(rule, DailyLimitRule):
        if rule.max_minutes_per_day < 1:
            errors.append(PolicyValidationError(path, "max_minutes_per_day must be >= 1"))
        _validate_tz(rule.timezone, path, errors)

    elif isinstance(rule, SessionLimitRule):
        if rule.max_minutes_per_session < 1:
            errors.append(PolicyValidationError(path, "max_minutes_per_session must be >= 1"))

    elif isinstance(rule, BedtimeBanRule):
        _validate_tz(rule.timezone, path, errors)
        if rule.start_time == rule.end_time:
            errors.append(PolicyValidationError(path, "bedtime start_time and end_time must differ"))

    elif isinstance(rule, TimeWindowRule):
        _validate_tz(rule.timezone, path, errors)
        if rule.days_of_week is not None:
            for d in rule.days_of_week:
                if d < 0 or d > 6:
                    errors.append(PolicyValidationError(path, f"invalid day_of_week: {d}"))

    elif isinstance(rule, ExceptionRule):
        if rule.valid_until is not None:
            try:
                from datetime import datetime
                datetime.fromisoformat(rule.valid_until.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                errors.append(PolicyValidationError(path, f"invalid valid_until ISO datetime: {rule.valid_until}"))

    elif isinstance(rule, AndRule):
        for i, child in enumerate(rule.rules):
            _validate_rule(child, f"{path}.rules[{i}]", errors)

    elif isinstance(rule, OrRule):
        for i, child in enumerate(rule.rules):
            _validate_rule(child, f"{path}.rules[{i}]", errors)

    elif isinstance(rule, NotRule):
        _validate_rule(rule.rule, f"{path}.rule", errors)


def _validate_tz(tz_name: str, path: str, errors: list[PolicyValidationError]) -> None:
    try:
        pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        errors.append(PolicyValidationError(path, f"unknown timezone: {tz_name}"))
