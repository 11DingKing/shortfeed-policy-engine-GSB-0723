from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ReasonCode
from app.domain.policy.ast import PolicyAST
from app.domain.policy.checker import validate_policy
from app.domain.policy.engine import EvaluationContext, evaluate_policy
from app.infrastructure.clock import Clock, SystemClock
from app.infrastructure.id_generator import IdGenerator, Uuid4Generator
from app.infrastructure.repositories.policy_repo import PolicyRepository


def preview_policy_static(
    ast: PolicyAST,
    user_id: str,
    user_age: int | None = None,
    user_timezone: str = "UTC",
    current_time: datetime | None = None,
    daily_used_minutes: int = 0,
    session_active_seconds: int = 0,
    has_active_exception: bool = False,
) -> dict[str, Any]:
    errors = validate_policy(ast)
    if errors:
        return {
            "allowed": False,
            "reason_code": ReasonCode.POLICY_VALIDATION_FAILED.value,
            "trace": [],
            "validation_errors": [e.to_dict() for e in errors],
        }
    now = current_time or datetime.now(timezone.utc)
    ctx = EvaluationContext(
        user_id=user_id,
        user_age=user_age,
        user_timezone=user_timezone,
        current_time=now,
        daily_used_minutes=daily_used_minutes,
        session_active_seconds=session_active_seconds,
        has_active_exception=has_active_exception,
    )
    result = evaluate_policy(ast, ctx)
    return {
        "allowed": result.allowed,
        "reason_code": result.reason_code.value,
        "trace": [result.trace.to_dict()],
    }


class PolicyService:
    def __init__(
        self,
        db: AsyncSession,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._db = db
        self._clock = clock or SystemClock()
        self._id_gen = id_generator or Uuid4Generator()
        self._policies = PolicyRepository(db)

    async def publish_policy(
        self,
        tenant_id: str,
        name: str,
        ast: PolicyAST,
        description: str | None = None,
        created_by: str | None = None,
    ) -> tuple[dict[str, Any] | None, ReasonCode, list[str]]:
        errors = validate_policy(ast)
        if errors:
            return None, ReasonCode.POLICY_VALIDATION_FAILED, [e.to_dict() for e in errors]
        version = await self._policies.next_version(tenant_id)
        policy_id = self._id_gen.new_id()
        policy = await self._policies.create(
            policy_id=policy_id,
            tenant_id=tenant_id,
            version=version,
            name=name,
            description=description,
            ast_json=ast.model_dump(),
            created_by=created_by,
        )
        return self._policy_to_dict(policy), ReasonCode.OK, []

    async def preview_policy(
        self,
        ast: PolicyAST,
        user_id: str,
        user_age: int | None = None,
        user_timezone: str = "UTC",
        current_time: datetime | None = None,
        daily_used_minutes: int = 0,
        session_active_seconds: int = 0,
        has_active_exception: bool = False,
    ) -> dict[str, Any]:
        errors = validate_policy(ast)
        if errors:
            return {
                "allowed": False,
                "reason_code": ReasonCode.POLICY_VALIDATION_FAILED.value,
                "trace": [],
                "validation_errors": [e.to_dict() for e in errors],
            }
        now = current_time or self._clock.now()
        ctx = EvaluationContext(
            user_id=user_id,
            user_age=user_age,
            user_timezone=user_timezone,
            current_time=now,
            daily_used_minutes=daily_used_minutes,
            session_active_seconds=session_active_seconds,
            has_active_exception=has_active_exception,
        )
        result = evaluate_policy(ast, ctx)
        return {
            "allowed": result.allowed,
            "reason_code": result.reason_code.value,
            "trace": [result.trace.to_dict()],
        }

    async def get_policy(self, tenant_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is not None:
            policy = await self._policies.get_by_version(tenant_id, version)
        else:
            policy = await self._policies.get_latest(tenant_id)
        if policy is None:
            return None
        return self._policy_to_dict(policy)

    async def list_policies(self, tenant_id: str) -> list[dict[str, Any]]:
        policies = await self._policies.list_versions(tenant_id)
        return [self._policy_to_dict(p) for p in policies]

    @staticmethod
    def _policy_to_dict(policy: Any) -> dict[str, Any]:
        return {
            "id": policy.id,
            "tenant_id": policy.tenant_id,
            "version": policy.version,
            "name": policy.name,
            "description": policy.description,
            "ast": policy.ast_json,
            "is_active": policy.is_active,
            "created_at": policy.created_at,
            "created_by": policy.created_by,
        }
