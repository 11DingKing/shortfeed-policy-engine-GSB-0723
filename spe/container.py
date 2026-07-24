"""Application container — wires configuration, infrastructure, and services."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .domain.clock import Clock, SystemClock
from .domain.ids import IdGenerator, UUID4IdGenerator
from .domain.services.policy_service import PolicyService
from .domain.services.replay_service import ReplayService
from .domain.services.session_service import SessionService
from .infra.db.outbox import EventPublisher, NullPublisher, OutboxDispatcher
from .infra.db.session import Database
from .infra.db.uow import SqlAlchemyUnitOfWork


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    clock: Clock
    id_gen: IdGenerator
    db: Database
    uow: SqlAlchemyUnitOfWork
    policies: PolicyService
    sessions: SessionService
    replays: ReplayService
    outbox: OutboxDispatcher

    async def startup(self) -> None:
        await self.db.create_all()
        await self.outbox.start()

    async def shutdown(self) -> None:
        await self.outbox.stop()
        await self.db.dispose()


def build_container(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    id_gen: IdGenerator | None = None,
    publisher: EventPublisher | None = None,
    init_db: bool = True,
) -> AppContainer:
    s = settings or Settings.from_env()
    clk = clock or SystemClock()
    gen = id_gen or UUID4IdGenerator()
    db = Database(s.database_url, echo=s.echo_sql)
    uow = SqlAlchemyUnitOfWork(db.session_factory)
    policies = PolicyService(uow=uow, clock=clk, id_gen=gen)
    sessions = SessionService(uow=uow, clock=clk, id_gen=gen)
    replays = ReplayService(uow=uow)
    outbox = OutboxDispatcher(
        db.session_factory,
        publisher=publisher or NullPublisher(),
        poll_interval=s.outbox_poll_interval_seconds,
    )
    return AppContainer(
        settings=s,
        clock=clk,
        id_gen=gen,
        db=db,
        uow=uow,
        policies=policies,
        sessions=sessions,
        replays=replays,
        outbox=outbox,
    )
