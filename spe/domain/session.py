"""Session domain state.

A session represents one continuous viewing engagement by a user. It is pinned
to the policy version that was active when it *started*; publishing a newer
policy version never changes an in-flight or historical session's settlement.

Usage accounting is heartbeat-driven. Each heartbeat carries a monotonically
increasing sequence number and the client's cumulative watched-time marker; the
engine credits only the *new* delta, ignores duplicates/out-of-order beats, and
clamps implausible gaps. Watch-time is attributed to the local day in which it
occurred, so a session that crosses local midnight settles correctly on both
days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SessionStatus(StrEnum):
    """Lifecycle states of a session."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


@dataclass
class DailyUsage:
    """Accumulated watch-time for one (session, local-day) pair, in seconds."""

    local_day: str
    seconds: int = 0


@dataclass
class Session:
    """The session aggregate.

    ``last_seq`` and ``watched_seconds_marker`` implement idempotent, ordered
    accounting: heartbeats below the high-water sequence are ignored, and the
    watched-time marker records the last accepted cumulative client marker so
    only the incremental delta is credited.
    """

    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None

    # Idempotent accounting state.
    last_seq: int = 0
    watched_seconds_marker: int = 0
    total_watched_seconds: int = 0

    # Per-local-day settlement buckets, keyed by ISO date string.
    daily: dict[str, DailyUsage] = field(default_factory=dict)

    def daily_seconds(self, local_day: str) -> int:
        bucket = self.daily.get(local_day)
        return bucket.seconds if bucket else 0

    def add_usage(self, local_day: str, seconds: int) -> None:
        if seconds <= 0:
            return
        bucket = self.daily.get(local_day)
        if bucket is None:
            bucket = DailyUsage(local_day=local_day)
            self.daily[local_day] = bucket
        bucket.seconds += seconds
        self.total_watched_seconds += seconds
