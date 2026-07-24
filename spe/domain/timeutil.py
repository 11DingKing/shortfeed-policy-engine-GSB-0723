"""Time utilities for daily-quota accounting across timezones."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class DateSpan:
    local_date: date
    seconds: float


def local_date_for(when_utc: datetime, tz_name: str) -> date:
    return when_utc.astimezone(ZoneInfo(tz_name)).date()


def split_by_local_midnight(
    start_utc: datetime, end_utc: datetime, tz_name: str
) -> list[DateSpan]:
    """Allocate elapsed seconds in ``[start_utc, end_utc)`` to local dates.

    Returns a list of :class:`DateSpan` ordered chronologically.  When the
    interval crosses the user's local midnight, the elapsed seconds are split
    at that boundary so that the daily-quota rollup is correct.  The result is
    deterministic and depends only on the inputs.
    """
    if end_utc <= start_utc:
        return []
    tz = ZoneInfo(tz_name)
    spans: list[DateSpan] = []
    cursor = start_utc
    while cursor < end_utc:
        local_cursor = cursor.astimezone(tz)
        next_midnight_local = datetime.combine(
            local_cursor.date() + timedelta(days=1), time(0, 0), tzinfo=tz
        )
        next_midnight_utc = next_midnight_local.astimezone(start_utc.tzinfo)
        seg_end = min(end_utc, next_midnight_utc)
        seconds = (seg_end - cursor).total_seconds()
        if seconds > 0:
            spans.append(DateSpan(local_date=local_cursor.date(), seconds=seconds))
        cursor = seg_end
    return spans
