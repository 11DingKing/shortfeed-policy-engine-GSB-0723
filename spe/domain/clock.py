"""Injectable clock abstraction.

Services depend on :class:`Clock` (a :class:`typing.Protocol`) rather than on
:func:`datetime.datetime.now` so that:

* unit tests can supply deterministic timelines;
* cross-midnight and retry behaviour is reproducible;
* the database and domain layers never call wall-clock time directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware ``datetime`` in UTC."""


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FakeClock:
    """A deterministic clock used by tests and by the replay engine.

    Time is advanced explicitly via :meth:`advance` or fixed via :meth:`set`.
    """

    current: datetime

    def __init__(self, start: datetime | None = None) -> None:
        if start is None:
            start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        elif start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        object.__setattr__(self, "current", start)

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        object.__setattr__(self, "current", value)

    def advance(self, seconds: float = 0.0) -> None:
        from datetime import timedelta

        object.__setattr__(self, "current", self.current + timedelta(seconds=seconds))
