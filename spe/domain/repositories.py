"""Repository protocols (the domain's persistence port).

The domain services depend only on these abstract interfaces, never on
SQLAlchemy. The concrete adapters live in :mod:`spe.infra.db.repositories`.
"""

from __future__ import annotations

from typing import Protocol

from spe.domain.events import DomainEvent
from spe.domain.policy_ast import PolicyDocument
from spe.domain.session import Session


class PolicyRecord(Protocol):
    """A published, immutable policy version."""

    @property
    def id(self) -> str: ...
    @property
    def tenant_id(self) -> str: ...
    @property
    def version(self) -> int: ...
    @property
    def document(self) -> PolicyDocument: ...


class PolicyRepository(Protocol):
    """Reads and writes versioned policy documents."""

    async def next_version(self, tenant_id: str) -> int:
        """Return the next monotonically increasing version for a tenant."""
        ...

    async def add(
        self, tenant_id: str, version: int, document: PolicyDocument, policy_id: str
    ) -> PolicyRecord:
        """Persist a new immutable policy version."""
        ...

    async def get_active(self, tenant_id: str) -> PolicyRecord | None:
        """Return the latest published version for a tenant, if any."""
        ...

    async def get_version(self, tenant_id: str, version: int) -> PolicyRecord | None:
        """Return a specific published version, if it exists for the tenant."""
        ...

    async def get_by_id(self, tenant_id: str, policy_id: str) -> PolicyRecord | None:
        """Return a policy by its id, scoped to the tenant."""
        ...


class SessionRepository(Protocol):
    """Reads and writes session aggregates."""

    async def add(self, session: Session) -> None: ...

    async def get(self, tenant_id: str, session_id: str) -> Session | None: ...

    async def get_active_for_user(self, tenant_id: str, user_id: str) -> Session | None: ...

    async def get_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> Session | None: ...

    async def save(self, session: Session, idempotency_key: str | None = None) -> None: ...


class OutboxRepository(Protocol):
    """Appends domain events for later publication."""

    async def add(self, event: DomainEvent) -> None: ...


class HeartbeatSink(Protocol):
    """Persists accepted heartbeats so a session can be replayed later."""

    async def record(
        self,
        tenant_id: str,
        session_id: str,
        seq: int,
        watched_seconds_total: int,
        credited_seconds: int,
        occurred_at: object,
    ) -> None: ...
