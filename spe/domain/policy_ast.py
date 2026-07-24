"""Policy AST value objects.

A policy is an ordered list of *rules*.  Each rule carries a *predicate* (a
boolean expression AST) and an *effect* that fires when the predicate matches.
The AST is fully described by Pydantic v2 models so that (a) incoming JSON is
validated structurally and (b) the static checker / interpreter both operate on
the same typed tree.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .reason_codes import ReasonCode

# ---------------------------------------------------------------------------
# Expression nodes
# ---------------------------------------------------------------------------


class NodeType(str, Enum):
    TRUE = "true"
    FALSE = "false"
    AND = "and"
    OR = "or"
    NOT = "not"
    AGE_GTE = "age_gte"
    AGE_LT = "age_lt"
    TIMEZONE_IN = "timezone_in"
    LOCAL_TIME_BETWEEN = "local_time_between"
    DAILY_USED_GTE = "daily_used_gte"
    SESSION_USED_GTE = "session_used_gte"
    HAS_APPROVAL = "has_approval"


class _ExprBase(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class TrueNode(_ExprBase):
    op: Literal[NodeType.TRUE] = NodeType.TRUE


class FalseNode(_ExprBase):
    op: Literal[NodeType.FALSE] = NodeType.FALSE


class NotNode(_ExprBase):
    op: Literal[NodeType.NOT] = NodeType.NOT
    child: Expr


class AndNode(_ExprBase):
    op: Literal[NodeType.AND] = NodeType.AND
    children: list[Expr] = Field(min_length=1)


class OrNode(_ExprBase):
    op: Literal[NodeType.OR] = NodeType.OR
    children: list[Expr] = Field(min_length=1)


class AgeGte(_ExprBase):
    op: Literal[NodeType.AGE_GTE] = NodeType.AGE_GTE
    n: int = Field(ge=0, le=150)


class AgeLt(_ExprBase):
    op: Literal[NodeType.AGE_LT] = NodeType.AGE_LT
    n: int = Field(ge=0, le=150)


class TimezoneIn(_ExprBase):
    op: Literal[NodeType.TIMEZONE_IN] = NodeType.TIMEZONE_IN
    zones: list[str] = Field(min_length=1)

    @field_validator("zones")
    @classmethod
    def _validate_zones(cls, v: list[str]) -> list[str]:
        for z in v:
            try:
                ZoneInfo(z)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"unknown IANA timezone: {z!r}") from exc
        return v


class LocalTimeBetween(_ExprBase):
    """True when the *local* time of the user is in [start, end).

    Times are ``HH:MM`` in 24h notation.  If ``start`` equals ``end`` the
    window is treated as empty.  If ``start`` > ``end`` the window wraps
    midnight.
    """

    op: Literal[NodeType.LOCAL_TIME_BETWEEN] = NodeType.LOCAL_TIME_BETWEEN
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    reason: str | None = None  # optional tag for bed-time rules

    @model_validator(mode="after")
    def _validate_window(self) -> LocalTimeBetween:
        sh, sm = (int(x) for x in self.start.split(":"))
        eh, em = (int(x) for x in self.end.split(":"))
        if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
            raise ValueError("time components out of range")
        # start==end means the window is empty (24h? we treat as empty to be safe)
        return self


class DailyUsedGte(_ExprBase):
    op: Literal[NodeType.DAILY_USED_GTE] = NodeType.DAILY_USED_GTE
    seconds: int = Field(ge=0)


class SessionUsedGte(_ExprBase):
    op: Literal[NodeType.SESSION_USED_GTE] = NodeType.SESSION_USED_GTE
    seconds: int = Field(ge=0)


class HasApproval(_ExprBase):
    op: Literal[NodeType.HAS_APPROVAL] = NodeType.HAS_APPROVAL
    approval: str = Field(min_length=1)


Expr = Annotated[
    TrueNode | FalseNode | NotNode | AndNode | OrNode | AgeGte | AgeLt | TimezoneIn | LocalTimeBetween | DailyUsedGte | SessionUsedGte | HasApproval,
    Field(discriminator="op"),
]

# Rebuild forward references for the recursive Union.
NotNode.model_rebuild()
AndNode.model_rebuild()
OrNode.model_rebuild()


# ---------------------------------------------------------------------------
# Effects and Rules
# ---------------------------------------------------------------------------


class EffectKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    LIMIT_DAILY = "limit_daily"
    LIMIT_SESSION = "limit_session"


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EffectKind
    reason: str | None = None  # human-readable note; reason code is derived by kind

    @property
    def limit_seconds(self) -> int | None:
        return None


class AllowEffect(Effect):
    kind: Literal[EffectKind.ALLOW] = EffectKind.ALLOW


class DenyEffect(Effect):
    kind: Literal[EffectKind.DENY] = EffectKind.DENY
    reason_code: ReasonCode

    @field_validator("reason_code")
    @classmethod
    def _must_be_denial(cls, v: ReasonCode) -> ReasonCode:
        allowed = {
            ReasonCode.DENIED_AGE_BELOW_MINIMUM,
            ReasonCode.DENIED_AGE_ABOVE_MAXIMUM,
            ReasonCode.DENIED_TIMEZONE_FORBIDDEN,
            ReasonCode.DENIED_BEDTIME_WINDOW,
            ReasonCode.DENIED_NO_APPROVAL,
        }
        if v not in allowed:
            raise ValueError(f"reason_code for deny must be one of {sorted(r.value for r in allowed)}")
        return v


class LimitDailyEffect(Effect):
    kind: Literal[EffectKind.LIMIT_DAILY] = EffectKind.LIMIT_DAILY
    seconds: int = Field(ge=1)


class LimitSessionEffect(Effect):
    kind: Literal[EffectKind.LIMIT_SESSION] = EffectKind.LIMIT_SESSION
    seconds: int = Field(ge=1)


Effect_ = Annotated[
    AllowEffect | DenyEffect | LimitDailyEffect | LimitSessionEffect,
    Field(discriminator="kind"),
]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    description: str = ""
    when: Expr
    effect: Effect_


class PolicyDocument(BaseModel):
    """A full policy AST with metadata."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str = ""
    rules: list[Rule] = Field(min_length=0)

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, v: list[Rule]) -> list[Rule]:
        seen: set[str] = set()
        for r in v:
            if r.id in seen:
                raise ValueError(f"duplicate rule id: {r.id}")
            seen.add(r.id)
        return v


# ---------------------------------------------------------------------------
# Evaluation context and result types
# ---------------------------------------------------------------------------


class EvaluationContext(BaseModel):
    """Snapshot of facts used when evaluating a policy."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    user_age: int = Field(ge=0, le=150)
    user_timezone: str
    now_utc: datetime
    daily_used_seconds: int = Field(ge=0)
    session_used_seconds: int = Field(ge=0)
    approvals: set[str] = Field(default_factory=set)

    @field_validator("user_timezone")
    @classmethod
    def _validate_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {v!r}") from exc
        return v

    def local_now(self) -> datetime:
        return self.now_utc.astimezone(ZoneInfo(self.user_timezone))

    def local_date(self) -> date:
        return self.local_now().date()


class TraceEntry(BaseModel):
    """One evaluation step in a deterministic explanation trace."""

    model_config = ConfigDict(extra="forbid")
    rule_id: str | None
    node: NodeType
    result: bool | None = None
    detail: str = ""


class EvaluationResult(BaseModel):
    """Outcome of evaluating a full :class:`PolicyDocument`."""

    model_config = ConfigDict(extra="forbid")
    allowed: bool
    rule_id: str | None
    reason: str  # ReasonCode value
    effect: EffectKind | None
    limit_seconds: int | None = None
    trace: list[TraceEntry]


# ---------------------------------------------------------------------------
# Public helper to parse arbitrary JSON/dict into the AST
# ---------------------------------------------------------------------------


def parse_policy(raw: dict) -> PolicyDocument:
    return PolicyDocument.model_validate(raw)


def parse_expr(raw: dict) -> Expr:  # type: ignore[valid-type]
    # Pydantic discriminated union accepts dicts; wrap with a model for validation
    class _Wrap(BaseModel):
        e: Expr

    return _Wrap.model_validate({"e": raw}).e  # type: ignore[return-value]
