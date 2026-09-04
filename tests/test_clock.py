from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.clock import MarketClock

ET = ZoneInfo("America/New_York")


def test_weekend_is_closed() -> None:
    saturday = datetime(2026, 3, 14, 12, 0, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: saturday)
    assert clock.skip_reason() == "weekend"
    assert not clock.is_open()
    assert not clock.can_enter()


def test_holiday_is_closed() -> None:
    new_years = datetime(2026, 1, 1, 12, 0, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: new_years)
    assert clock.skip_reason() == "holiday"
    assert not clock.is_session_day()


def test_rth_mid_morning_allows_entries() -> None:
    now = datetime(2026, 3, 10, 10, 30, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: now)
    assert clock.is_open()
    assert clock.can_enter()
    assert clock.skip_reason() is None


def test_open_buffer() -> None:
    now = datetime(2026, 3, 10, 9, 35, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: now, open_buffer_minutes=15)
    assert clock.is_open()
    assert clock.skip_reason() == "open_buffer"


def test_lunch_window() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: now)
    assert clock.skip_reason() == "lunch"


def test_close_buffer() -> None:
    now = datetime(2026, 3, 10, 15, 50, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: now, close_buffer_minutes=15)
    assert clock.skip_reason() == "close_buffer"


def test_after_hours_session_closed() -> None:
    now = datetime(2026, 3, 10, 18, 0, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: now)
    assert clock.skip_reason() == "session_closed"
