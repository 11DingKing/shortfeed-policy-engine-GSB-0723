"""Timezone and local-day helpers.

All wall-clock policy rules (bedtime curfews, daily-limit resets) operate in the
policy's local timezone. These helpers convert between UTC instants and the
local calendar so that cross-midnight behaviour is handled in exactly one place.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


def local_now(instant: datetime, timezone: str) -> datetime:
    """Convert a UTC instant to local wall-clock time in ``timezone``."""
    return instant.astimezone(ZoneInfo(timezone))


def local_time(instant: datetime, timezone: str) -> time:
    """Return the local time-of-day for a UTC instant."""
    return local_now(instant, timezone).timetz().replace(tzinfo=None)


def local_day(instant: datetime, timezone: str) -> date:
    """Return the local calendar date for a UTC instant.

    This is the key that daily usage is bucketed by; because it is computed in
    the policy timezone, usage rolls over exactly at local midnight regardless
    of the server's own timezone.
    """
    return local_now(instant, timezone).date()


def local_day_key(instant: datetime, timezone: str) -> str:
    """Return the local day as an ISO ``YYYY-MM-DD`` string (stable storage key)."""
    return local_day(instant, timezone).isoformat()
