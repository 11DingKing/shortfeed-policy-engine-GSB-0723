"""Timezone and local-day helpers.

All wall-clock policy rules (bedtime curfews, daily-limit resets) operate in the
policy's local timezone. These helpers convert between UTC instants and the
local calendar so that cross-midnight behaviour is handled in exactly one place.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
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


def age_at(birth_date: date, instant: datetime, timezone: str) -> int:
    """Compute a user's whole-year age as of ``instant`` in the policy timezone.

    Age is intentionally derived from the birth date and the *current local
    calendar date* rather than being stored, so it advances correctly over the
    life of a long session and is consistent between live evaluation and replay.
    """
    today = local_day(instant, timezone)
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (0 if had_birthday else 1)


def next_local_midnight(instant: datetime, timezone: str) -> datetime:
    """Return the first local midnight strictly after ``instant`` as a UTC instant."""
    tz = ZoneInfo(timezone)
    aware = instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)
    local = aware.astimezone(tz)
    next_day = local.date() + timedelta(days=1)
    midnight_local = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz)
    return midnight_local.astimezone(UTC)


def split_watch_window(
    now: datetime, seconds: int, timezone: str
) -> list[tuple[str, int]]:
    """Split a watch interval that *ends* at ``now`` into per-local-day segments.

    The interval is modelled as the ``seconds`` immediately preceding ``now``:
    ``[now - seconds, now]``. Any local midnight boundaries inside that interval
    split the duration precisely (in whole seconds) between the adjacent local
    days, returned in chronological order. This is how a single heartbeat that
    straddles local midnight is attributed accurately to both dates.
    """
    if seconds <= 0:
        return []
    # Normalise to UTC-aware, whole-second boundaries so durations sum exactly.
    end = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    end = end.astimezone(UTC).replace(microsecond=0)
    cursor = end - timedelta(seconds=seconds)
    remaining = seconds
    segments: list[tuple[str, int]] = []
    while remaining > 0:
        day = local_day(cursor, timezone).isoformat()
        boundary = next_local_midnight(cursor, timezone)
        span = int((boundary - cursor).total_seconds())
        take = remaining if span <= 0 or span >= remaining else span
        segments.append((day, take))
        remaining -= take
        cursor = cursor + timedelta(seconds=take)
    return segments
