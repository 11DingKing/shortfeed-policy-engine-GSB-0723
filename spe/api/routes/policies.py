"""Policy authoring, validation, preview, and version listing endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...container import AppContainer
from ...domain.reason_codes import ReasonCode
from ...domain.services.policy_service import PolicyService
from ...domain.services.responses import ServiceResponse
from ..deps import get_container, require_tenant
from ..schemas.http import (
    CreatePolicyRequest,
    PreviewRequest,
    PublishVersionRequest,
    ValidateRequest,
)
from ..web import respond

router = APIRouter(tags=["policies"])


@router.post("/policies")
async def create_policy(
    body: CreatePolicyRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.policies.create_policy(
        tenant_id=tenant,
        document=body.document,
        author=body.author,
        idempotency_key=body.idempotency_key,
    )
    return respond(result)


@router.post("/policies/{policy_id}/versions")
async def publish_version(
    policy_id: str,
    body: PublishVersionRequest,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    result = await container.policies.publish_version(
        tenant_id=tenant,
        policy_id=policy_id,
        document=body.document,
        author=body.author,
    )
    return respond(result)


@router.get("/policies/{policy_id}/current")
async def get_current(
    policy_id: str,
    tenant: str = Depends(require_tenant),
    container: AppContainer = Depends(get_container),
):
    current = await container.policies.get_current(tenant_id=tenant, policy_id=policy_id)
    if current is None:
        return respond(ServiceResponse(ok=False, reason=ReasonCode.POLICY_NOT_FOUND))
    doc, version = current
    return respond(
        ServiceResponse(
            ok=True,
            reason=ReasonCode.OK,
            data={
                "policy_id": policy_id,
                "version": version,
                "document": doc.model_dump(mode="json"),
            },
        )
    )


@router.post("/policies/validate")
async def validate_policy(body: ValidateRequest, _: str = Depends(require_tenant)):
    return respond(PolicyService.validate(body.document))


@router.post("/policies/preview")
async def preview_policy(body: PreviewRequest, _: str = Depends(require_tenant)):
    return respond(PolicyService.preview(body.document, body.context))
