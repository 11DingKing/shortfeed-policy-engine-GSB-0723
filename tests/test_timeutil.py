"""Tests for timezone-aware time splitting across local midnight."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from spe.domain.timeutil import local_date_for, split_by_local_midnight


def test_no_crossing_single_span():
    tz = "Asia/Shanghai"
    start = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=100)
    spans = split_by_local_midnight(start, end, tz)
    assert len(spans) == 1
    assert abs(spans[0].seconds - 100) < 1e-6
    assert spans[0].local_date.isoformat() == "2026-06-01"


def test_crossing_midnight_splits_in_two():
    tz = "Asia/Shanghai"  # +8
    # 2026-06-01 23:50:00 local = 2026-06-01 15:50:00 UTC
    start = datetime(2026, 6, 1, 15, 50, 0, tzinfo=UTC)
    end = start + timedelta(minutes=20)  # 16:10 UTC = 00:10 local next day
    spans = split_by_local_midnight(start, end, tz)
    assert len(spans) == 2
    assert spans[0].local_date.isoformat() == "2026-06-01"
    assert spans[1].local_date.isoformat() == "2026-06-02"
    total = sum(s.seconds for s in spans)
    assert abs(total - 20 * 60) < 1e-6


def test_negative_interval_returns_empty():
    tz = "UTC"
    start = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    end = start - timedelta(seconds=10)
    assert split_by_local_midnight(start, end, tz) == []


def test_local_date_for():
    tz = "America/Los_Angeles"  # UTC-7 (PDT in June)
    # 2026-06-01 05:00 UTC = 2026-05-31 22:00 PDT
    when = datetime(2026, 6, 1, 5, 0, 0, tzinfo=UTC)
    assert local_date_for(when, tz).isoformat() == "2026-05-31"
