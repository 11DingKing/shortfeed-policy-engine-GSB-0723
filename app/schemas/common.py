from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ReasonCode


class ErrorResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=False)
    error: str
    reason_code: ReasonCode
    message: str
    detail: dict[str, Any] | None = None


class ExplanationTrace(BaseModel):
    model_config = ConfigDict(use_enum_values=False)
    rule_type: str
    rule_description: str
    passed: bool
    reason_code: ReasonCode
    detail: dict[str, Any] = Field(default_factory=dict)
    children: list[ExplanationTrace] = Field(default_factory=list)


ExplanationTrace.model_rebuild()
