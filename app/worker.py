from __future__ import annotations

import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.config import settings
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.db.models.outbox import OutboxMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] outbox-worker: %(message)s",
)
logger = logging.getLogger("outbox-worker")

BATCH_SIZE = 50
POLL_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 5


class OutboxPublisher:
    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        logger.info(
            "PUBLISH event_type=%s payload=%s",
            event_type,
            json.dumps(payload, default=str, ensure_ascii=False),
        )


async def process_batch(publisher: OutboxPublisher) -> int:
    async with async_session_factory() as db:
        try:
            stmt = (
                select(OutboxMessage)
                .where(
                    OutboxMessage.processed.is_(False),
                    OutboxMessage.retry_count < MAX_RETRIES,
                )
                .order_by(OutboxMessage.created_at.asc())
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            result = await db.execute(stmt)
            messages = list(result.scalars().all())

            if not messages:
                return 0

            processed = 0
            for msg in messages:
                try:
                    await publisher.publish(msg.event_type, msg.payload)
                    msg.processed = True
                    msg.processed_at = datetime.now(timezone.utc)
                    processed += 1
                    logger.info("Processed outbox message id=%s event=%s", msg.id, msg.event_type)
                except Exception as exc:
                    msg.retry_count += 1
                    msg.error = str(exc)
                    logger.error("Failed to process message %s: %s", msg.id, exc)

            await db.commit()
            return processed
        except Exception:
            await db.rollback()
            raise


async def run_worker() -> None:
    logger.info("Outbox worker starting (batch_size=%d, poll_interval=%.1fs)", BATCH_SIZE, POLL_INTERVAL_SECONDS)
    publisher = OutboxPublisher()

    stop = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received, draining...")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    total_processed = 0
    while not stop.is_set():
        try:
            count = await process_batch(publisher)
            total_processed += count
            if count > 0:
                logger.info("Batch processed: %d messages (total: %d)", count, total_processed)
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            logger.error("Error in outbox processing loop: %s", exc, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Outbox worker stopped. Total processed: %d", total_processed)


if __name__ == "__main__":
    asyncio.run(run_worker())
