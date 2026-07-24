"""Shared pytest fixtures."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from spe.config import Settings
from spe.container import AppContainer, build_container
from spe.domain.clock import FakeClock
from spe.domain.ids import SequentialIdGenerator


@pytest.fixture
def tmp_db_url(tmp_path: Path) -> str:
    db_file = tmp_path / "test.db"
    return f"sqlite+aiosqlite:///{db_file}"


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def seq_ids() -> SequentialIdGenerator:
    return SequentialIdGenerator()


@pytest_asyncio.fixture
async def container(tmp_db_url: str, fake_clock: FakeClock, seq_ids: SequentialIdGenerator) -> AsyncIterator[AppContainer]:
    settings = Settings(database_url=tmp_db_url, echo_sql=False, outbox_poll_interval_seconds=1000.0)
    c = build_container(settings, clock=fake_clock, id_gen=seq_ids)
    await c.db.create_all()
    try:
        yield c
    finally:
        await c.db.dispose()


@pytest.fixture
def sample_policy() -> dict:
    """A realistic teen-protection policy.

    - 0..12 denied (below minimum age)
    - 13..17 allowed but daily cap 60 min, session cap 15 min
    - 18+ allowed with 120 min daily cap
    - Bedtime 22:00-06:00 local time denied for <18
    - 'parental-exception' approval overrides
    """
    return {
        "name": "teen-default",
        "description": "default shortvideo policy for teenage users",
        "rules": [
            {
                "id": "age-under-13",
                "description": "Under 13 not allowed",
                "when": {"op": "age_lt", "n": 13},
                "effect": {"kind": "deny", "reason_code": "DENIED_AGE_BELOW_MINIMUM"},
            },
            {
                "id": "teen-session-cap",
                "description": "Teens capped at 15 min/session",
                "when": {
                    "op": "and",
                    "children": [
                        {"op": "age_lt", "n": 18},
                        {"op": "session_used_gte", "seconds": 900},
                    ],
                },
                "effect": {"kind": "limit_session", "seconds": 900},
            },
            {
                "id": "teen-daily-cap",
                "description": "Teens capped at 60 min/day",
                "when": {
                    "op": "and",
                    "children": [
                        {"op": "age_lt", "n": 18},
                        {"op": "daily_used_gte", "seconds": 3600},
                    ],
                },
                "effect": {"kind": "limit_daily", "seconds": 3600},
            },
            {
                "id": "adult-daily-cap",
                "description": "Adults capped at 120 min/day",
                "when": {
                    "op": "and",
                    "children": [
                        {"op": "age_gte", "n": 18},
                        {"op": "daily_used_gte", "seconds": 7200},
                    ],
                },
                "effect": {"kind": "limit_daily", "seconds": 7200},
            },
            {
                "id": "bedtime",
                "description": "No scrolling 22:00-06:00 for under-18s",
                "when": {
                    "op": "and",
                    "children": [
                        {"op": "age_lt", "n": 18},
                        {"op": "local_time_between", "start": "22:00", "end": "06:00"},
                        {"op": "not", "child": {"op": "has_approval", "approval": "parental-exception"}},
                    ],
                },
                "effect": {"kind": "deny", "reason_code": "DENIED_BEDTIME_WINDOW"},
            },
            {
                "id": "allow-all",
                "description": "Allow otherwise",
                "when": {"op": "true"},
                "effect": {"kind": "allow"},
            },
        ],
    }
