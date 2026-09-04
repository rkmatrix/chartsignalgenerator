from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pa.config import Settings
from pa.domain.models import CalendarEvent

ET = ZoneInfo("America/New_York")

# Approximate 2026 US macro prints (fixture). Replace via API when a vendor key exists.
_MACRO: list[tuple[str, datetime, str]] = [
    ("FOMC", datetime(2026, 1, 28, 14, 0, tzinfo=ET), "fomc"),
    ("CPI", datetime(2026, 2, 11, 8, 30, tzinfo=ET), "cpi"),
    ("NFP", datetime(2026, 3, 6, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 3, 18, 14, 0, tzinfo=ET), "fomc"),
    ("CPI", datetime(2026, 3, 11, 8, 30, tzinfo=ET), "cpi"),
    ("NFP", datetime(2026, 4, 3, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 5, 6, 14, 0, tzinfo=ET), "fomc"),
    ("CPI", datetime(2026, 4, 10, 8, 30, tzinfo=ET), "cpi"),
    ("NFP", datetime(2026, 5, 1, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 6, 17, 14, 0, tzinfo=ET), "fomc"),
    ("NFP", datetime(2026, 6, 5, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 7, 29, 14, 0, tzinfo=ET), "fomc"),
    ("NFP", datetime(2026, 8, 7, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 9, 16, 14, 0, tzinfo=ET), "fomc"),
    ("NFP", datetime(2026, 9, 4, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 11, 4, 14, 0, tzinfo=ET), "fomc"),
    ("NFP", datetime(2026, 10, 2, 8, 30, tzinfo=ET), "nfp"),
    ("FOMC", datetime(2026, 12, 9, 14, 0, tzinfo=ET), "fomc"),
    ("NFP", datetime(2026, 12, 4, 8, 30, tzinfo=ET), "nfp"),
]

# ETFs rarely have single-name earnings; include liquid names used in tests/fixtures.
_EARNINGS: dict[str, list[datetime]] = {
    "AAPL": [datetime(2026, 1, 29, 16, 0, tzinfo=ET), datetime(2026, 4, 30, 16, 0, tzinfo=ET)],
    "MSFT": [datetime(2026, 1, 27, 16, 0, tzinfo=ET), datetime(2026, 4, 28, 16, 0, tzinfo=ET)],
    "NVDA": [datetime(2026, 2, 25, 16, 0, tzinfo=ET), datetime(2026, 5, 27, 16, 0, tzinfo=ET)],
}


class MarketCalendar:
    def __init__(self, settings: Settings, extra_earnings: dict[str, list[datetime]] | None = None) -> None:
        self.settings = settings
        self._earnings = dict(_EARNINGS)
        if extra_earnings:
            self._earnings.update(extra_earnings)

    def events(self, now: datetime) -> list[CalendarEvent]:
        out = [
            CalendarEvent(name=name, ts=ts, kind=kind)
            for name, ts, kind in _MACRO
            if abs((ts - now).total_seconds()) < 14 * 86400
        ]
        for ticker, times in self._earnings.items():
            for ts in times:
                if abs((ts - now).total_seconds()) < 14 * 86400:
                    out.append(CalendarEvent(name=f"{ticker} earnings", ts=ts, kind="earnings", tickers=[ticker]))
        out.sort(key=lambda e: e.ts)
        return out

    def macro_blackout(self, now: datetime) -> CalendarEvent | None:
        window = timedelta(minutes=self.settings.macro_blackout_minutes)
        for name, ts, kind in _MACRO:
            if ts - window <= now <= ts + timedelta(minutes=15):
                return CalendarEvent(name=name, ts=ts, kind=kind)
        return None

    def earnings_tickers(self, now: datetime) -> set[str]:
        days = self.settings.earnings_blackout_days
        blocked: set[str] = set()
        for ticker, times in self._earnings.items():
            for ts in times:
                if abs((ts.date() - now.date()).days) <= days:
                    blocked.add(ticker.upper())
        blocked |= self.settings.blackout_tickers
        return blocked
