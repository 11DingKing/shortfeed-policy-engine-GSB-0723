"""Injectable clock abstractions.

Time is never read directly from :func:`datetime.now`; every component that needs
the current instant depends on a :class:`Clock`. Tests inject a
:class:`FixedClock` (or advance it manually) so behaviour is fully deterministic
and reproducible across process restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current instant, always timezone-aware UTC."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC ``datetime``."""
        ...


class SystemClock:
    """Production clock backed by the operating system wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic clock for tests and replay.

    The clock stays put until explicitly advanced, which makes it possible to
    exercise cross-midnight boundaries, ordered/late heartbeats and other
    time-sensitive flows without real sleeps.
    """

    def __init__(self, start: datetime) -> None:
        self._now = _ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = _ensure_utc(value)

    def advance(self, seconds: float) -> datetime:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)
        return self._now


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
