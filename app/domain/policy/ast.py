from __future__ import annotations

from datetime import time as dt_time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RuleNode(BaseModel):
    model_config = ConfigDict(polymorphic_on="type", discriminator="type", extra="forbid")


class AgeGateRule(RuleNode):
    type: Literal["age_gate"] = "age_gate"
    min_age: int = Field(..., ge=0, le=200, description="Minimum allowed age in years")


class DailyLimitRule(RuleNode):
    type: Literal["daily_limit"] = "daily_limit"
    max_minutes_per_day: int = Field(..., ge=1, le=1440, description="Maximum usage minutes per calendar day")
    timezone: str = Field(default="UTC", description="IANA timezone for day boundary calculation")


class SessionLimitRule(RuleNode):
    type: Literal["session_limit"] = "session_limit"
    max_minutes_per_session: int = Field(..., ge=1, le=1440, description="Maximum consecutive session duration in minutes")


class BedtimeBanRule(RuleNode):
    type: Literal["bedtime_ban"] = "bedtime_ban"
    start_time: dt_time = Field(..., description="Local bedtime start (HH:MM)")
    end_time: dt_time = Field(..., description="Local bedtime end (HH:MM)")
    timezone: str = Field(default="UTC", description="IANA timezone for bedtime window")


class TimeWindowRule(RuleNode):
    type: Literal["time_window"] = "time_window"
    start_time: dt_time = Field(..., description="Allowed window start (HH:MM)")
    end_time: dt_time = Field(..., description="Allowed window end (HH:MM)")
    timezone: str = Field(default="UTC", description="IANA timezone for window")
    days_of_week: list[int] | None = Field(
        default=None,
        description="Allowed days (0=Monday..6=Sunday); null means every day",
    )

    @field_validator("days_of_week")
    @classmethod
    def _validate_days(cls, v: list[int] | None) -> list[int] | None:
        if v is not None:
            for d in v:
                if d < 0 or d > 6:
                    raise ValueError(f"days_of_week must be 0-6, got {d}")
        return v


class ExceptionRule(RuleNode):
    type: Literal["exception"] = "exception"
    user_ids: list[str] = Field(default_factory=list, description="Users exempt from policy enforcement")
    reason: str | None = Field(default=None, description="Human-readable approval reason")
    valid_until: str | None = Field(
        default=None,
        description="ISO-8601 expiration datetime; null means no expiration",
    )


class AndRule(RuleNode):
    type: Literal["and"] = "and"
    rules: list[PolicyRule] = Field(..., min_length=1, description="All sub-rules must pass")


class OrRule(RuleNode):
    type: Literal["or"] = "or"
    rules: list[PolicyRule] = Field(..., min_length=1, description="At least one sub-rule must pass")


class NotRule(RuleNode):
    type: Literal["not"] = "not"
    rule: PolicyRule = Field(..., description="The sub-rule to negate")


PolicyRule = Annotated[
    Union[
        AgeGateRule,
        DailyLimitRule,
        SessionLimitRule,
        BedtimeBanRule,
        TimeWindowRule,
        ExceptionRule,
        AndRule,
        OrRule,
        NotRule,
    ],
    Field(discriminator="type"),
]

AndRule.model_rebuild()
OrRule.model_rebuild()
NotRule.model_rebuild()


class PolicyAST(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(..., ge=1, description="Policy schema version")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    rule: PolicyRule = Field(..., description="Root rule node")


class TimeOfDay(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    minute: int = Field(..., ge=0, le=59)


def validate_timezone(tz_name: str) -> None:
    import pytz
    try:
        pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        raise ValueError(f"Unknown timezone: {tz_name}")
