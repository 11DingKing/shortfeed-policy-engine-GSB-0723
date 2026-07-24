from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import SessionStatus


class SessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(..., min_length=1, max_length=64)
    user_age: int | None = Field(default=None, ge=0, le=200)
    user_timezone: str = Field(default="UTC")
    policy_version: int | None = None


class SessionHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(..., ge=0)


class SessionPauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


class SessionResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class SessionEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    total_active_seconds: int
    total_active_minutes: float = 0
    last_evaluation_reason: str | None
    last_evaluation_detail: dict[str, Any] | None
    allowed: bool = True
    reason_code: str = "ok"
    message: str | None = None


class SessionActionResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)
    session: SessionResponse
    action: str
    success: bool
    reason_code: str
    message: str
    trace: list[dict[str, Any]] | None = None


class UsageQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    user_id: str
    date: str
    total_sessions: int
    total_active_seconds: int
    total_active_minutes: float
    daily_limit_minutes: int | None = None
    remaining_minutes: float | None = None
    current_session_id: str | None = None
    current_session_status: str | None = None
