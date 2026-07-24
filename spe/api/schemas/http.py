"""Request/response schemas for the HTTP API.

The policy document and evaluation-context bodies are intentionally ``dict`` so
that the API accepts arbitrary JSON ASTs and forwards them to the domain
validators (which produce detailed issues).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = False
    reason: str
    message: str = ""
    data: dict[str, Any] | None = None


class ApiEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    reason: str
    data: dict[str, Any] | None = None
    trace: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


class CreatePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]
    author: str | None = None
    idempotency_key: str | None = None


class PublishVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]
    author: str | None = None


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]
    context: dict[str, Any]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class StartSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    user_age: int = Field(ge=0, le=150)
    user_timezone: str = Field(min_length=1)
    approvals: list[str] = Field(default_factory=list)
    initial_sequence: int = 0
    idempotency_key: str = Field(min_length=1)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=0)
    idempotency_key: str | None = None


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str | None = None


class UsageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    user_timezone: str = Field(min_length=1)
