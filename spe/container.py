"""Composition root / dependency container.

Wires domain services to their concrete infrastructure adapters. The clock and
id generator are injected here so tests can swap in deterministic
implementations without touching business code.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spe.config import Settings, get_settings
from spe.domain.clock import Clock, SystemClock
from spe.domain.ids import IdGenerator, UuidGenerator
from spe.domain.services.policy_service import PolicyService
from spe.domain.services.session_service import SessionService
from spe.infra.db.repositories.repositories import (
    SqlHeartbeatRepository,
    SqlOutboxRepository,
    SqlPolicyRepository,
    SqlSessionRepository,
)
from spe.infra.db.session import make_engine, make_session_factory


@dataclass
class Services:
    """Per-request services bound to one database session."""

    policy_service: PolicyService
    session_service: SessionService
    heartbeats: SqlHeartbeatRepository
    policies: SqlPolicyRepository
    clock: Clock


class Container:
    """Process-wide singletons (engine, factory, clock, ids)."""

    def __init__(
        self,
        settings: Settings | None = None,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.clock: Clock = clock or SystemClock()
        self.ids: IdGenerator = ids or UuidGenerator()
        self.engine: AsyncEngine = make_engine(
            self.settings.database_url, echo=self.settings.echo_sql
        )
        self.session_factory: async_sessionmaker[AsyncSession] = make_session_factory(
            self.engine
        )

    def services_for(self, db: AsyncSession) -> Services:
        """Build request-scoped services bound to ``db``."""
        now = self.clock.now()
        policies = SqlPolicyRepository(db, clock_now=now)
        sessions = SqlSessionRepository(db)
        outbox = SqlOutboxRepository(db)
        heartbeats = SqlHeartbeatRepository(db)
        policy_service = PolicyService(policies, outbox, self.clock, self.ids)
        session_service = SessionService(
            sessions,
            policies,
            outbox,
            self.clock,
            self.ids,
            heartbeats=heartbeats,
            heartbeat_max_gap_seconds=self.settings.heartbeat_max_gap_seconds,
        )
        return Services(
            policy_service=policy_service,
            session_service=session_service,
            heartbeats=heartbeats,
            policies=policies,
            clock=self.clock,
        )

    async def dispose(self) -> None:
        await self.engine.dispose()
