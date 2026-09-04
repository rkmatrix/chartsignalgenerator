from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.clock import MarketClock
from pa.open_session.playbook import apply_playbook, bucket_for, playbook_for, session_window
from pa.open_session.scan import scan_open
from pa.open_session.setups import Candidate
from tests.conftest import make_settings
from tests.test_open_session import bullish_open, dump_after_spike

ET = ZoneInfo("America/New_York")


def _c(ticker, strategy, family, conv=1.2) -> Candidate:
    return Candidate(ticker, "call", strategy, family, conv, 100.0, 99.0, strategy, 100.0)


def test_windows_match_the_clock() -> None:
    assert session_window(30) == "first_hour"
    assert session_window(75) == "mid_morning"
    assert session_window(100) == "after_90"
    assert session_window(270) == "afternoon"  # 14:00 ET


def test_buckets() -> None:
    assert bucket_for("SPY") == "index"
    assert bucket_for("SPX") == "index"
    assert bucket_for("TSLA") == "wild"
    assert bucket_for("NVDA") == "wild"
    assert bucket_for("AAPL") == "mega"
    assert bucket_for("XOM") == "slow"


def test_spy_first_hour_is_opening_drive() -> None:
    pb = playbook_for("SPY", 35)
    assert pb.window == "first_hour"
    assert "orb" in pb.allow
    assert "gap_and_go" in pb.allow
    assert "momentum_burst" in pb.allow
    assert "bb_rejection" not in pb.allow
    assert "pullback_to_vwap" not in pb.allow


def test_spy_after_90_drops_fresh_orb() -> None:
    pb = playbook_for("SPY", 100)
    assert "orb" not in pb.allow
    assert "gap_and_go" not in pb.allow
    assert "pullback_to_vwap" in pb.allow
    assert "bb_rejection" in pb.allow
    assert "level_retest" in pb.allow


def test_tsla_first_hour_only_gap() -> None:
    pb = playbook_for("TSLA", 25)
    assert pb.allow == frozenset({"gap_and_go"})
    kept, _ = apply_playbook(
        [_c("TSLA", "orb", "level"), _c("TSLA", "momentum_burst", "momentum"), _c("TSLA", "gap_and_go", "gap", 1.1)],
        "TSLA",
        25,
    )
    assert {c.strategy for c in kept} == {"gap_and_go"}


def test_tsla_after_90_is_the_window() -> None:
    pb = playbook_for("TSLA", 95)
    assert "pullback_to_vwap" in pb.allow
    assert "level_retest" in pb.allow
    assert "bb_rejection" in pb.allow
    assert "ema50_break" in pb.allow
    assert "momentum_burst" in pb.allow
    assert "orb" not in pb.allow
    assert "gap_and_go" not in pb.allow


def test_scan_still_takes_spy_open(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": bullish_open("SPY")},
    )
    assert tape["strongest"] is None
    spy = next(r for r in tape["rows"] if r["ticker"] == "SPY")
    assert spy["fused"]["direction"] == "call"
    assert spy["playbook"]["window"] == "first_hour"
    assert spy["playbook"]["bucket"] == "index"


def test_scan_dump_at_65m_still_puts(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": dump_after_spike("SPY")},
    )
    assert tape["strongest"] is not None
    assert tape["strongest"]["direction"] == "put"


def test_scan_silences_tsla_open_noise(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["TSLA"],
        bars_by_ticker={"TSLA": bullish_open("TSLA")},
    )
    assert tape["strongest"] is None
    row = next(r for r in tape["rows"] if r["ticker"] == "TSLA")
    assert "orb" not in row["candidates"]
    assert row["playbook"]["bucket"] == "wild"
    assert row["playbook"]["window"] == "first_hour"
