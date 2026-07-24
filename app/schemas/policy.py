from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.policy.ast import PolicyAST


class PolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    ast: PolicyAST
    created_by: str | None = None


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    version: int
    name: str
    description: str | None
    ast: dict[str, Any]
    is_active: bool
    created_at: datetime
    created_by: str | None = None


class PolicyPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ast: PolicyAST
    context: EvaluationContext


class EvaluationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    user_age: int | None = None
    user_timezone: str = "UTC"
    current_time: datetime | None = None
    daily_used_minutes: int = 0
    session_active_seconds: int = 0
    has_active_exception: bool = False


class PolicyValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class PolicyPreviewResponse(BaseModel):
    allowed: bool
    reason_code: str
    trace: list[dict[str, Any]]
