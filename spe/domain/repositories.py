"""Repository abstractions used by the domain services.

Concrete implementations live in :mod:`spe.infra.db.repositories`.  Defining
them as :class:`typing.Protocol` keeps the domain layer pure and allows the
replay engine to supply in-memory fakes.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from typing import Protocol

from .events import DomainEvent
from .session import SessionAggregate


class PolicyVersionRow(Protocol):
    tenant_id: str
    policy_id: str
    version: int
    document: dict
    is_current: bool
    published_at: datetime
    published_by: str | None


class PolicyRepository(Protocol):
    async def create(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        name: str,
        description: str,
        created_by: str | None,
        now: datetime,
    ) -> None: ...
    async def exists(self, *, tenant_id: str, policy_id: str) -> bool: ...
    async def publish_version(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        document: dict,
        published_by: str | None,
        now: datetime,
    ) -> tuple[str, int]: ...
    async def get_current(self, *, tenant_id: str, policy_id: str) -> PolicyVersionRow | None: ...
    async def get_version(
        self, *, tenant_id: str, policy_id: str, version: int
    ) -> PolicyVersionRow | None: ...
    async def list_versions(self, *, tenant_id: str, policy_id: str) -> Sequence[PolicyVersionRow]: ...


class SessionRepository(Protocol):
    async def insert(self, session: SessionAggregate) -> None: ...
    async def get(self, *, tenant_id: str, session_id: str) -> SessionAggregate | None: ...
    async def get_active_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> SessionAggregate | None: ...
    async def update(self, session: SessionAggregate) -> None: ...


class UsageRepository(Protocol):
    async def add_seconds(
        self, *, tenant_id: str, user_id: str, local_date: date, seconds: float, now: datetime
    ) -> None: ...
    async def get_daily(
        self, *, tenant_id: str, user_id: str, local_date: date
    ) -> float: ...


class OutboxRepository(Protocol):
    async def add(self, event: DomainEvent) -> None: ...
    async def add_batch(self, events: Sequence[DomainEvent]) -> None: ...
    async def list_for_aggregate(
        self, *, tenant_id: str, aggregate_id: str
    ) -> Sequence[DomainEvent]: ...


class IdempotencyRepository(Protocol):
    async def get(
        self, *, tenant_id: str, idempotency_key: str
    ) -> dict | None: ...
    async def store(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        request_type: str,
        request_hash: str,
        response: dict,
        now: datetime,
    ) -> None: ...


class UnitOfWork(Protocol):
    """Transactional boundary.

    ``run`` receives the repository bundle and must either commit atomically or
    roll back.  Repositories share the same DB transaction so that outbox
    events, session mutations, and idempotency rows are committed together.
    """

    async def run(self, fn: Callable[[RepoBundle], Awaitable[object]]): ...


class RepoBundle(Protocol):
    policies: PolicyRepository
    sessions: SessionRepository
    usage: UsageRepository
    outbox: OutboxRepository
    idempotency: IdempotencyRepository
