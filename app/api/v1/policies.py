from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import PolicySvc, TenantId
from app.domain.enums import ReasonCode
from app.schemas.policy import (
    PolicyCreateRequest,
    PolicyResponse,
    PolicyPreviewRequest,
    PolicyPreviewResponse,
    PolicyValidationResult,
)
from app.domain.policy.checker import validate_policy as validate_policy_ast
from app.services.policy_service import preview_policy_static

router = APIRouter(prefix="/policies", tags=["policies"])


@router.post("", response_model=PolicyResponse, status_code=201)
async def publish_policy(
    tenant_id: TenantId,
    svc: PolicySvc,
    body: PolicyCreateRequest,
):
    errors = validate_policy_ast(body.ast)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Policy validation failed",
                "reason_code": ReasonCode.POLICY_VALIDATION_FAILED.value,
                "errors": [e.to_dict() for e in errors],
            },
        )
    result, rc, errs = await svc.publish_policy(
        tenant_id=tenant_id,
        name=body.name,
        ast=body.ast,
        description=body.description,
        created_by=body.created_by,
    )
    if rc != ReasonCode.OK:
        raise HTTPException(status_code=400, detail={"reason_code": rc.value, "errors": errs})
    return result


@router.get("/{version}", response_model=PolicyResponse)
async def get_policy(tenant_id: TenantId, svc: PolicySvc, version: int):
    result = await svc.get_policy(tenant_id, version)
    if result is None:
        raise HTTPException(status_code=404, detail={"reason_code": ReasonCode.POLICY_NOT_FOUND.value})
    return result


@router.get("", response_model=list[PolicyResponse])
async def list_policies(tenant_id: TenantId, svc: PolicySvc):
    return await svc.list_policies(tenant_id)


@router.post("/preview", response_model=PolicyPreviewResponse)
async def preview_policy(tenant_id: TenantId, body: PolicyPreviewRequest):
    return preview_policy_static(
        ast=body.ast,
        user_id=body.context.user_id,
        user_age=body.context.user_age,
        user_timezone=body.context.user_timezone,
        current_time=body.context.current_time,
        daily_used_minutes=body.context.daily_used_minutes,
        session_active_seconds=body.context.session_active_seconds,
        has_active_exception=body.context.has_active_exception,
    )


@router.post("/validate", response_model=PolicyValidationResult)
async def validate_policy_endpoint(body: PolicyCreateRequest):
    errors = validate_policy_ast(body.ast)
    return PolicyValidationResult(valid=len(errors) == 0, errors=[e.to_dict() for e in errors])
