"""HTTP request/response schemas (Pydantic v2).

These are the wire contracts, kept separate from the domain AST and aggregates.
Every field is explicitly typed; reason codes are surfaced as the
:class:`ReasonCode` enum so clients receive stable string values.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from spe.domain.policy_ast import PolicyDocument
from spe.domain.reason_codes import ReasonCode
from spe.domain.session import SessionStatus

# --- Policy -----------------------------------------------------------------


class PublishPolicyRequest(BaseModel):
    """Publish a new immutable policy version."""

    document: PolicyDocument


class PublishPolicyResponse(BaseModel):
    policy_id: str
    tenant_id: str
    version: int
    name: str


class ValidationIssueOut(BaseModel):
    path: str
    message: str


class CheckPolicyResponse(BaseModel):
    ok: bool
    issues: list[ValidationIssueOut] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    """A hypothetical evaluation context for a dry-run preview.

    The user's age is derived from ``birth_date`` and the evaluation instant in
    the policy timezone, matching how live sessions compute age.
    """

    user_id: str
    birth_date: date
    daily_usage_seconds: int = Field(default=0, ge=0)
    session_elapsed_seconds: int = Field(default=0, ge=0)
    at: datetime | None = None
    version: int | None = None


class TraceStepOut(BaseModel):
    rule: str
    outcome: str
    reason: str | None
    detail: str


class PreviewResponse(BaseModel):
    allowed: bool
    reason: ReasonCode
    trace: list[TraceStepOut]


# --- Sessions ---------------------------------------------------------------


class StartSessionRequest(BaseModel):
    user_id: str
    birth_date: date
    idempotency_key: str | None = None


class HeartbeatRequest(BaseModel):
    seq: int = Field(ge=1)
    watched_seconds_total: int = Field(ge=0)


class SessionOut(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    birth_date: date
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None
    total_watched_seconds: int


class ActionResponse(BaseModel):
    """Uniform envelope for every session lifecycle action."""

    ok: bool
    reason: ReasonCode
    session: SessionOut | None = None
    trace: list[TraceStepOut] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


# --- Replay -----------------------------------------------------------------


class ReplayStepOut(BaseModel):
    seq: int
    credited_seconds: int
    per_day: dict[str, int]
    total_watched_seconds: int
    age: int
    allowed: bool
    reason: str
    trace: list[TraceStepOut]


class ReplayResponse(BaseModel):
    session_id: str
    policy_version: int
    steps: list[ReplayStepOut]
