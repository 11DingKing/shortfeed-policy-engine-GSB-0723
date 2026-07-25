"""Session domain state.

A session represents one continuous viewing engagement by a user. It is pinned
to the policy version that was active when it *started*; publishing a newer
policy version never changes an in-flight or historical session's settlement.

Usage accounting is heartbeat-driven. Each heartbeat carries a monotonically
increasing sequence number and the client's cumulative watched-time marker; the
engine credits only the *new* delta, ignores duplicates/out-of-order beats, and
clamps implausible gaps. Crucially, **daily** watch-time is *not* owned by the
session — it lives in an authoritative per-(tenant, user, local-day) ledger, so
ending a session and starting a new one cannot reset a user's daily quota. The
session itself only tracks its own elapsed watch-time (for the session limit),
the heartbeat high-water marks, and the user's birth date (age is derived
dynamically at evaluation time).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class SessionStatus(StrEnum):
    """Lifecycle states of a session."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


@dataclass
class Session:
    """The session aggregate.

    ``last_seq`` and ``watched_seconds_marker`` implement idempotent, ordered
    accounting: heartbeats below the high-water sequence are ignored, and the
    watched-time marker records the last accepted cumulative client marker so
    only the incremental delta is credited. ``total_watched_seconds`` is the
    session's own elapsed watch-time, used to enforce the session limit.
    """

    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    birth_date: date
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None

    # Idempotent accounting state.
    last_seq: int = 0
    watched_seconds_marker: int = 0
    total_watched_seconds: int = 0
