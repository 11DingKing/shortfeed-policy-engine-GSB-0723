"""Helpers shared across routers."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from ..domain.reason_codes import REASON_CODE_DESCRIPTIONS, ReasonCode
from ..domain.services.responses import ServiceResponse


def _http_status(reason: ReasonCode, ok: bool) -> int:
    if ok:
        return status.HTTP_200_OK
    mapping = {
        ReasonCode.POLICY_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        ReasonCode.POLICY_VERSION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        ReasonCode.SESSION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        ReasonCode.TENANT_ISOLATION_VIOLATION: status.HTTP_403_FORBIDDEN,
        ReasonCode.CONCURRENT_SESSION_BLOCKED: status.HTTP_409_CONFLICT,
        ReasonCode.POLICY_INVALID: status.HTTP_422_UNPROCESSABLE_ENTITY,
        ReasonCode.POLICY_AST_TYPE_ERROR: status.HTTP_422_UNPROCESSABLE_ENTITY,
        ReasonCode.POLICY_AST_CONTRADICTION: status.HTTP_422_UNPROCESSABLE_ENTITY,
        ReasonCode.POLICY_AST_UNREACHABLE_RULE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return mapping.get(reason, status.HTTP_400_BAD_REQUEST)


def respond(result: ServiceResponse) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": result.ok,
        "reason": result.reason.value,
        "message": REASON_CODE_DESCRIPTIONS.get(result.reason, ""),
        "data": result.data or None,
    }
    return JSONResponse(status_code=_http_status(result.reason, result.ok), content=body)


def require_ok(result: ServiceResponse) -> dict[str, Any]:
    if not result.ok:
        raise HTTPException(
            status_code=_http_status(result.reason, False),
            detail={
                "ok": False,
                "reason": result.reason.value,
                "message": REASON_CODE_DESCRIPTIONS.get(result.reason, ""),
                "data": result.data or None,
            },
        )
    return result.data
