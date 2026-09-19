from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.open_session.ledger import _expire_stale, _sanitize_mark

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 10, 11, 0, tzinfo=ET)


def test_expired_contract_with_a_real_quote_is_closed_and_graded() -> None:
    row = {
        "status": "open", "expiry": "2026-09-02", "entry": 2.00,
        "mark": 0.40, "bid": 0.38, "quoted": True,
    }
    assert _expire_stale(row, NOW) is True
    assert row["status"] == "closed"
    assert row["reason"] == "expired"
    assert row["exit"] == 0.38  # books the bid, not the mid
    assert row["pnl_dollars"] == -162.0
    assert row["prediction"] == "FAIL"


def test_expired_contract_never_quoted_is_closed_but_not_graded() -> None:
    # Closing this at the entry would invent a 0% PASS out of nothing.
    row = {"status": "open", "expiry": "2026-09-02", "entry": 2.00, "mark": 2.00}
    assert _expire_stale(row, NOW) is True
    assert row["status"] == "closed"
    assert row["reason"] == "expired_unquoted"
    assert row["prediction"] == "unknown"


def test_live_and_future_contracts_are_untouched() -> None:
    same_day = {"status": "open", "expiry": "2026-09-10", "entry": 1.0, "mark": 1.2}
    assert _expire_stale(same_day, NOW) is False
    assert same_day["status"] == "open"

    later = {"status": "open", "expiry": "2026-09-18", "entry": 1.0, "mark": 1.2}
    assert _expire_stale(later, NOW) is False
    assert later["status"] == "open"


def test_quote_death_guard_still_protects_delayed_yahoo_ticks() -> None:
    row = {"entry": 2.00, "mark": 0.10, "quote_source": "yahoo"}
    _sanitize_mark(row, held=1.0)
    assert row["mark"] == 2.00
    assert row["quote_reject"] is True


def test_real_time_nbbo_crash_is_kept() -> None:
    # 0DTE really does halve in minutes; with a live NBBO that is a loss, not a bad tick.
    row = {"entry": 2.00, "mark": 0.10, "quote_source": "uw"}
    _sanitize_mark(row, held=1.0)
    assert row["mark"] == 0.10
    assert "quote_reject" not in row
