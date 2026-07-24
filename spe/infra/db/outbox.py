"""Outbox dispatcher.

The transactional outbox pattern guarantees that state changes and event
publication are atomic: writers insert an :class:`OutboxRow` in the same
transaction as the business mutation.  A separate dispatcher polls for
un-dispatched rows, hands them to a pluggable publisher, and marks them
dispatched.  The default publisher is a no-op that simply logs; production
deployments inject a Kafka / NATS / webhook publisher.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import OutboxRow

log = logging.getLogger("spe.outbox")


class EventPublisher(Protocol):
    async def publish(self, event_type: str, payload: dict, *, tenant_id: str, aggregate_id: str) -> None: ...


@dataclass(slots=True)
class NullPublisher:
    async def publish(self, event_type: str, payload: dict, *, tenant_id: str, aggregate_id: str) -> None:
        log.info("outbox event %s tenant=%s aggregate=%s payload=%s", event_type, tenant_id, aggregate_id, payload)


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher | None = None,
        poll_interval: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        self._factory = session_factory
        self._publisher: EventPublisher = publisher or NullPublisher()
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="spe-outbox-dispatcher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._drain_once()
            except Exception:  # pragma: no cover - defensive
                log.exception("outbox drain iteration failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)

    async def _drain_once(self) -> int:
        async with self._factory() as session:
            q = (
                select(OutboxRow)
                .where(OutboxRow.dispatched.is_(False))
                .order_by(OutboxRow.id.asc())
                .limit(self._batch_size)
            )
            rows = list((await session.execute(q)).scalars())
            for row in rows:
                try:
                    await self._publisher.publish(
                        row.event_type,
                        dict(row.payload or {}),
                        tenant_id=row.tenant_id,
                        aggregate_id=row.aggregate_id,
                    )
                except Exception:
                    log.exception("publish failed for outbox row %s", row.id)
                    continue
                row.dispatched = True
                row.dispatched_at = datetime.now(UTC)
            await session.commit()
            return len(rows)
