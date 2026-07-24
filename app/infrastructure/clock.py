from __future__ import annotations

import abc
from datetime import datetime, timezone, date
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self, tz_name: str | None = None) -> date: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self, tz_name: str | None = None) -> date:
        if tz_name:
            import pytz
            tz = pytz.timezone(tz_name)
            return datetime.now(tz).date()
        return datetime.now(timezone.utc).date()


class FixedClock:
    def __init__(self, fixed_now: datetime) -> None:
        if fixed_now.tzinfo is None:
            fixed_now = fixed_now.replace(tzinfo=timezone.utc)
        self._fixed_now = fixed_now

    def now(self) -> datetime:
        return self._fixed_now

    def today(self, tz_name: str | None = None) -> date:
        if tz_name:
            import pytz
            tz = pytz.timezone(tz_name)
            return self._fixed_now.astimezone(tz).date()
        return self._fixed_now.date()

    def advance(self, **kwargs) -> None:
        from dateutil.relativedelta import relativedelta
        self._fixed_now += relativedelta(**kwargs)
