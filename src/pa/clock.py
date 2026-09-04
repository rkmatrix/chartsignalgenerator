from __future__ import annotations

from datetime import date, datetime, time, timedelta
from collections.abc import Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# NYSE full-day closures (observed dates). Extend annually as needed.
NYSE_HOLIDAYS: set[date] = {
    date(2024, 1, 1),
    date(2024, 1, 15),
    date(2024, 2, 19),
    date(2024, 3, 29),
    date(2024, 5, 27),
    date(2024, 6, 19),
    date(2024, 7, 4),
    date(2024, 9, 2),
    date(2024, 11, 28),
    date(2024, 12, 25),
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
}

# 13:00 ET close
NYSE_EARLY_CLOSES: set[date] = {
    date(2024, 7, 3),
    date(2024, 11, 29),
    date(2024, 12, 24),
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 11, 27),
    date(2026, 12, 24),
    date(2027, 11, 26),
}

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
EARLY_CLOSE_TIME = time(13, 0)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


class MarketClock:
    """US/Eastern session clock with NYSE holidays and early closes."""

    def __init__(
        self,
        now_fn: Callable[[], datetime] | None = None,
        open_buffer_minutes: int = 15,
        lunch_start: str = "11:45",
        lunch_end: str = "13:15",
        close_buffer_minutes: int = 15,
    ) -> None:
        self._now_fn = now_fn
        self.open_buffer_minutes = open_buffer_minutes
        self.lunch_start = _parse_hhmm(lunch_start)
        self.lunch_end = _parse_hhmm(lunch_end)
        self.close_buffer_minutes = close_buffer_minutes

    def now(self) -> datetime:
        if self._now_fn:
            current = self._now_fn()
            if current.tzinfo is None:
                return current.replace(tzinfo=ET)
            return current.astimezone(ET)
        return datetime.now(ET)

    def session_close_time(self, day: date) -> time:
        if day in NYSE_EARLY_CLOSES:
            return EARLY_CLOSE_TIME
        return RTH_CLOSE

    def is_weekend(self, day: date | None = None) -> bool:
        day = day or self.now().date()
        return day.weekday() >= 5

    def is_holiday(self, day: date | None = None) -> bool:
        day = day or self.now().date()
        return day in NYSE_HOLIDAYS

    def is_session_day(self, day: date | None = None) -> bool:
        day = day or self.now().date()
        return not self.is_weekend(day) and not self.is_holiday(day)

    def is_open(self, at: datetime | None = None) -> bool:
        moment = (at or self.now()).astimezone(ET)
        day = moment.date()
        if not self.is_session_day(day):
            return False
        clock = moment.timetz().replace(tzinfo=None)
        return RTH_OPEN <= clock < self.session_close_time(day)

    def skip_reason(self, at: datetime | None = None) -> str | None:
        """Why new entries are blocked. None means entries are allowed."""
        moment = (at or self.now()).astimezone(ET)
        day = moment.date()
        if self.is_weekend(day):
            return "weekend"
        if self.is_holiday(day):
            return "holiday"
        if not self.is_open(moment):
            return "session_closed"

        clock = moment.time().replace(tzinfo=None)
        open_ok = (
            datetime.combine(day, RTH_OPEN) + timedelta(minutes=self.open_buffer_minutes)
        ).time()
        if clock < open_ok:
            return "open_buffer"

        close_at = datetime.combine(day, self.session_close_time(day))
        close_ok = (close_at - timedelta(minutes=self.close_buffer_minutes)).time()
        if clock >= close_ok:
            return "close_buffer"

        if self.lunch_start <= clock < self.lunch_end:
            return "lunch"
        return None

    def can_enter(self, at: datetime | None = None) -> bool:
        return self.skip_reason(at) is None
