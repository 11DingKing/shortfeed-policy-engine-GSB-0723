"""Policy application service.

Responsibilities:

* Authoring: create a policy, publish a new immutable version.
* Validation: run the static checker and return structured issues.
* Preview: evaluate an in-progress document against a sample context and
  return the full deterministic trace.
* Resolution: fetch the currently-published version for a tenant+policy so the
  session service can pin it at session start.
"""
from __future__ import annotations

from typing import Any

from ..clock import Clock
from ..events import DomainEvent, EventType
from ..ids import IdGenerator
from ..policy_ast import EvaluationContext, PolicyDocument, parse_policy
from ..policy_validator import ValidationIssue, preview, validate_policy
from ..reason_codes import ReasonCode
from ..repositories import RepoBundle, UnitOfWork
from .responses import ServiceResponse


class PolicyService:
    def __init__(self, *, uow: UnitOfWork, clock: Clock, id_gen: IdGenerator) -> None:
        self._uow = uow
        self._clock = clock
        self._id_gen = id_gen

    # ---- authoring ------------------------------------------------------
    async def create_policy(
        self,
        *,
        tenant_id: str,
        document: dict[str, Any],
        author: str | None = None,
        idempotency_key: str | None = None,
    ) -> ServiceResponse:
        doc = parse_policy(document)
        issues = validate_policy(doc)
        if issues:
            return ServiceResponse(
                ok=False,
                reason=ReasonCode.POLICY_INVALID,
                data={"issues": [_issue_dict(i) for i in issues]},
            )

        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            if idempotency_key:
                existing = await bundle.idempotency.get(
                    tenant_id=tenant_id, idempotency_key=idempotency_key
                )
                if existing and existing.get("request_type") == "policy.create":
                    return {"replay": existing["response"], "replayed": True}
            policy_id = self._id_gen.new_id()
            now = self._clock.now()
            await bundle.policies.create(
                tenant_id=tenant_id,
                policy_id=policy_id,
                name=doc.name,
                description=doc.description,
                created_by=author,
                now=now,
            )
            version_id, version_no = await bundle.policies.publish_version(
                tenant_id=tenant_id,
                policy_id=policy_id,
                document=document,
                published_by=author,
                now=now,
            )
            response = {
                "policy_id": policy_id,
                "version": version_no,
                "version_id": version_id,
                "name": doc.name,
            }
            if idempotency_key:
                await bundle.idempotency.store(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    request_type="policy.create",
                    request_hash=_hash_request(document),
                    response=response,
                    now=now,
                )
            await bundle.outbox.add(
                DomainEvent(
                    event_type=EventType.POLICY_CREATED,
                    aggregate_id=policy_id,
                    tenant_id=tenant_id,
                    occurred_at=now,
                    payload={"version": version_no, "name": doc.name},
                )
            )
            await bundle.outbox.add(
                DomainEvent(
                    event_type=EventType.POLICY_PUBLISHED,
                    aggregate_id=policy_id,
                    tenant_id=tenant_id,
                    occurred_at=now,
                    payload={"version": version_no},
                )
            )
            return {"response": response, "replayed": False}

        result = await self._uow.run(_tx)
        replayed = result["replayed"]
        if replayed:
            return ServiceResponse(
                ok=True, reason=ReasonCode.IDEMPOTENT_REPLAY, data=result["replay"]
            )
        return ServiceResponse(
            ok=True, reason=ReasonCode.POLICY_CREATED, data=result["response"]
        )

    async def publish_version(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        document: dict[str, Any],
        author: str | None = None,
    ) -> ServiceResponse:
        doc = parse_policy(document)
        issues = validate_policy(doc)
        if issues:
            return ServiceResponse(
                ok=False,
                reason=ReasonCode.POLICY_INVALID,
                data={"issues": [_issue_dict(i) for i in issues]},
            )

        async def _tx(bundle: RepoBundle) -> dict[str, Any]:
            if not await bundle.policies.exists(tenant_id=tenant_id, policy_id=policy_id):
                return {"missing": True}
            now = self._clock.now()
            version_id, version_no = await bundle.policies.publish_version(
                tenant_id=tenant_id,
                policy_id=policy_id,
                document=document,
                published_by=author,
                now=now,
            )
            await bundle.outbox.add(
                DomainEvent(
                    event_type=EventType.POLICY_PUBLISHED,
                    aggregate_id=policy_id,
                    tenant_id=tenant_id,
                    occurred_at=now,
                    payload={"version": version_no},
                )
            )
            return {
                "policy_id": policy_id,
                "version": version_no,
                "version_id": version_id,
            }

        result = await self._uow.run(_tx)
        if result.get("missing"):
            return ServiceResponse(ok=False, reason=ReasonCode.POLICY_NOT_FOUND)
        return ServiceResponse(ok=True, reason=ReasonCode.POLICY_PUBLISHED, data=result)

    # ---- read / preview -------------------------------------------------
    async def get_current(
        self, *, tenant_id: str, policy_id: str
    ) -> tuple[PolicyDocument, int] | None:
        async def _tx(bundle: RepoBundle):
            return await bundle.policies.get_current(tenant_id=tenant_id, policy_id=policy_id)

        row = await self._uow.run(_tx)
        if row is None:
            return None
        return parse_policy(row.document), row.version

    async def get_version(
        self, *, tenant_id: str, policy_id: str, version: int
    ) -> PolicyDocument | None:
        async def _tx(bundle: RepoBundle):
            return await bundle.policies.get_version(
                tenant_id=tenant_id, policy_id=policy_id, version=version
            )

        row = await self._uow.run(_tx)
        if row is None:
            return None
        return parse_policy(row.document)

    @staticmethod
    def validate(document: dict[str, Any]) -> ServiceResponse:
        try:
            doc = parse_policy(document)
        except Exception as exc:  # pydantic ValidationError
            return ServiceResponse(
                ok=False, reason=ReasonCode.POLICY_INVALID, data={"error": str(exc)}
            )
        issues = validate_policy(doc)
        return ServiceResponse(
            ok=not issues,
            reason=ReasonCode.POLICY_VALID if not issues else ReasonCode.POLICY_INVALID,
            data={"issues": [_issue_dict(i) for i in issues]},
        )

    @staticmethod
    def preview(document: dict[str, Any], context: dict[str, Any]) -> ServiceResponse:
        try:
            doc = parse_policy(document)
            ctx = EvaluationContext.model_validate(context)
        except Exception as exc:
            return ServiceResponse(
                ok=False, reason=ReasonCode.POLICY_INVALID, data={"error": str(exc)}
            )
        issues, result = preview(doc, ctx)
        return ServiceResponse(
            ok=True,
            reason=ReasonCode.POLICY_PREVIEW_OK,
            data={
                "issues": [_issue_dict(i) for i in issues],
                "result": result.model_dump(mode="json"),
            },
        )


def _issue_dict(i: ValidationIssue) -> dict[str, Any]:
    return {"code": i.code.value, "rule_id": i.rule_id, "message": i.message}


def _hash_request(document: dict[str, Any]) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
