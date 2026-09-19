from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.clock import MarketClock
from pa.open_session.calibrate import TARGET_ACCURACY, allows_take, fit_policy
from pa.open_session.learn import rebuild
from pa.open_session.scan import scan_open
from tests.conftest import make_settings
from tests.test_learn import _closed
from tests.test_open_session import bullish_open, dump_after_spike

ET = ZoneInfo("America/New_York")


def test_first_hour_is_watch_even_with_confluence(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    # The fixture's synthetic contract prices at $0.66, which risk_block now
    # refuses. This test is about the first-hour verdict, not the premium band,
    # so give it a contract the desk will actually carry.
    monkeypatch.setattr("pa.open_session.contract.synthetic_premium", lambda *a, **k: 2.00)
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": bullish_open("SPY")},
    )
    assert tape["signals"] == []
    spy = next(r for r in tape["book"] if r.get("ticker") == "SPY")
    assert str(spy.get("verdict")).upper() == "WATCH"


def test_dump_put_after_open_still_takes(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": dump_after_spike("SPY")},
    )
    assert tape["strongest"] is not None
    assert tape["strongest"]["verdict"] == "TAKE"
    assert tape["strongest"]["direction"] == "put"


def test_policy_skips_first_hour_and_hits_target_on_long_holds(tmp_path) -> None:
    settings = make_settings(tmp_path)
    from pa.open_session.ledger import save_book

    trades = [
        _closed("META", "call", -27.7, "2026-09-02"),
        _closed("GOOGL", "call", -36.0, "2026-09-02"),
        _closed("AMD", "call", 24.0, "2026-09-01"),
        _closed("PLTR", "put", 42.0, "2026-09-01"),
        _closed("NVDA", "call", 18.0, "2026-09-01"),
        _closed("AAPL", "put", 12.0, "2026-09-01"),
        _closed("MSFT", "call", 8.0, "2026-09-01"),
        _closed("AMZN", "put", 6.0, "2026-09-01"),
    ]
    for i, row in enumerate(trades):
        if i < 2:
            row["window"] = "first_hour"
            row["opened_at"] = "2026-09-02T09:50:00-04:00"
            row["closed_at"] = "2026-09-02T09:55:00-04:00"
        else:
            row["window"] = "after_90"
            row["opened_at"] = f"2026-09-01T11:{10 + i:02d}:00-04:00"
            row["closed_at"] = f"2026-09-01T11:{30 + i:02d}:00-04:00"
            row["id"] = f"{row['ticker']}-{row['direction']}-{row['window']}-{i}"
    save_book(settings.data_dir, {"trades": trades})
    state = rebuild(settings.data_dir)
    pol = state["policy"]
    assert pol["banned_windows"] == ["first_hour"]
    assert pol["in_sample"]["hit"] is True
    assert pol["in_sample"]["n"] >= 5
    assert pol["in_sample"]["passes"] / pol["in_sample"]["n"] >= TARGET_ACCURACY
    ok, _ = allows_take(window="first_hour", direction="call", strategies=["orb", "ema_align"], state=state)
    assert ok is False
    ok, _ = allows_take(window="after_90", direction="call", strategies=["ema_align", "momentum_burst"], state=state)
    assert ok is True


def test_stale_policy_still_blocks_first_hour() -> None:
    ok, _ = allows_take(
        window="first_hour",
        direction="call",
        strategies=["orb", "ema_align"],
        state={"policy": {"banned_windows": [], "banned_window_sides": [], "banned_stacks": []}},
    )
    assert ok is False


def test_ledger_demotes_first_hour_take(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    sync_book(
        settings.data_dir,
        [
            {
                "ticker": "META",
                "direction": "call",
                "verdict": "TAKE",
                "score": 90,
                "window": "first_hour",
                "strategies": ["orb", "ema_align"],
                "entry": 1.0,
            }
        ],
        now,
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert str(row.get("verdict")).upper() == "WATCH"
    assert row.get("window") == "first_hour"
