from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytz

from app.infrastructure.repositories.session_repo import split_seconds_by_local_day


def test_split_no_midnight_crossing():
    tz = "Asia/Shanghai"
    start = datetime(2026, 7, 24, 14, 0, 0, tzinfo=pytz.timezone(tz))
    end = datetime(2026, 7, 24, 14, 5, 0, tzinfo=pytz.timezone(tz))
    result = split_seconds_by_local_day(start, end, tz)
    assert len(result) == 1
    d = list(result.keys())[0]
    assert d.day == 24
    assert result[d] == 300


def test_split_crosses_midnight():
    tz = "Asia/Shanghai"
    sh = pytz.timezone(tz)
    start = datetime(2026, 7, 24, 23, 58, 0, tzinfo=sh)
    end = datetime(2026, 7, 25, 0, 2, 0, tzinfo=sh)
    result = split_seconds_by_local_day(start, end, tz)
    assert len(result) == 2
    assert result[datetime(2026, 7, 24).date()] == 120
    assert result[datetime(2026, 7, 25).date()] == 120


def test_split_crosses_midnight_with_timezone_offset():
    tz = "Asia/Shanghai"
    sh = pytz.timezone(tz)
    start = datetime(2026, 7, 24, 23, 59, 0, tzinfo=sh)
    end = datetime(2026, 7, 25, 0, 1, 0, tzinfo=sh)
    result = split_seconds_by_local_day(start, end, tz)
    assert sum(result.values()) == 120
    assert len(result) == 2


def test_split_exactly_at_midnight_boundary():
    tz = "UTC"
    start = datetime(2026, 7, 24, 23, 59, 59, tzinfo=timezone.utc)
    end = datetime(2026, 7, 25, 0, 0, 1, tzinfo=timezone.utc)
    result = split_seconds_by_local_day(start, end, tz)
    assert len(result) == 2
    assert result[datetime(2026, 7, 24).date()] == 1
    assert result[datetime(2026, 7, 25).date()] == 1


def test_split_spans_multiple_days():
    tz = "UTC"
    start = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
    result = split_seconds_by_local_day(start, end, tz)
    assert len(result) == 3
    assert result[datetime(2026, 7, 24).date()] == 12 * 3600
    assert result[datetime(2026, 7, 25).date()] == 24 * 3600
    assert result[datetime(2026, 7, 26).date()] == 12 * 3600


def test_split_utc_to_other_timezone():
    tz = "America/New_York"
    ny = pytz.timezone(tz)
    start = datetime(2026, 7, 24, 22, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 25, 5, 0, 0, tzinfo=timezone.utc)
    result = split_seconds_by_local_day(start, end, tz)
    local_start = start.astimezone(ny)
    assert local_start.hour == 18
    total = sum(result.values())
    assert total == 7 * 3600


def test_split_zero_duration():
    tz = "UTC"
    start = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
    result = split_seconds_by_local_day(start, start, tz)
    assert len(result) == 0


def test_split_sub_minute_duration():
    tz = "UTC"
    start = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=30)
    result = split_seconds_by_local_day(start, end, tz)
    assert len(result) == 1
    assert result[datetime(2026, 7, 24).date()] == 30
