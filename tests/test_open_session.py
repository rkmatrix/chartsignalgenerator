from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pa.babysitter.advise import (
    BREAKEVEN_FLOOR_PCT, RATCHET_KEEP, advise, breakeven_floor,
)
from pa.babysitter.feed import review_positions, save_watch
from pa.clock import MarketClock
from pa.domain.models import Bar, Timeframe
from pa.open_session.clock import session_phase
from pa.open_session.fuse import fuse, pick_strongest
from pa.open_session.levels import Levels
from pa.open_session.playbook import playbook_for
from pa.open_session.scan import scan_open
from pa.open_session.setups import Candidate, setups_for
from tests.conftest import make_settings

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 8, 31, tzinfo=ET)


def _bar(ticker: str, ts: datetime, close: float, high: float | None = None, low: float | None = None) -> Bar:
    high = close if high is None else high
    low = close if low is None else low
    return Bar(
        ticker=ticker,
        ts=ts,
        open=close,
        high=max(high, close),
        low=min(low, close),
        close=close,
        volume=1_000_000,
        timeframe=Timeframe.M1,
    )


def _grind(ticker: str, start: datetime, n: int, price: float, step: float = 0.02, high_pad: float = 0.15) -> list[Bar]:
    bars = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        px = round(price + step * i, 4)
        bars.append(_bar(ticker, ts, px, high=px + high_pad, low=px - 0.05))
    return bars


def bullish_open(ticker: str = "SPY") -> list[Bar]:
    """Premarket range, 15m ORB, then closes above ORB with EMA/VWAP aligned."""
    pm = _grind(ticker, DAY.replace(hour=9, minute=0), 20, 99.0, step=0.02, high_pad=0.2)
    orb = _grind(ticker, DAY.replace(hour=9, minute=30), 15, 100.2, step=0.04, high_pad=0.25)
    after = _grind(ticker, DAY.replace(hour=9, minute=45), 10, 101.05, step=0.02, high_pad=0.08)
    return pm + orb + after


def _cand(ticker, direction, strategy, family, conv=1.2, last=100.0) -> Candidate:
    return Candidate(ticker, direction, strategy, family, conv, 100.0, 99.0, strategy, last)


def test_session_phase_weekend_and_orb() -> None:
    weekend = datetime(2026, 8, 30, 21, 0, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: weekend)
    assert session_phase(weekend, clock) == "weekend"

    building = datetime(2026, 8, 31, 9, 40, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: building)
    assert session_phase(building, clock) == "orb_building"

    confirming = datetime(2026, 8, 31, 9, 46, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: confirming)
    assert session_phase(confirming, clock) == "confirming"

    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    clock = MarketClock(now_fn=lambda: hunt)
    assert session_phase(hunt, clock) == "hunt"


def test_pm_plus_orb_is_one_family() -> None:
    fused = fuse([_cand("SPY", "call", "premarket_breakout", "level"), _cand("SPY", "call", "orb", "level")])
    assert fused is None


def test_orb_plus_trend_is_confluence() -> None:
    fused = fuse(
        [
            _cand("SPY", "call", "orb", "level"),
            _cand("SPY", "call", "ema_align", "trend", conv=1.0),
        ]
    )
    assert fused is not None
    assert fused.direction == "call"
    assert set(fused.families) == {"level", "trend"}


def test_gap_may_stand_alone() -> None:
    fused = fuse([_cand("NVDA", "call", "gap_and_go", "gap", conv=1.1)])
    assert fused is not None
    assert fused.direction == "call"


def test_opposing_call_and_put_cancelled() -> None:
    fused = fuse(
        [
            _cand("QQQ", "call", "orb", "level"),
            _cand("QQQ", "put", "ema_align", "trend", conv=1.0),
        ]
    )
    assert fused is not None
    assert fused.vetoed is True
    assert fused.direction == "flat"


def test_pick_strongest_one_ticker() -> None:
    spy = fuse([_cand("SPY", "call", "orb", "level"), _cand("SPY", "call", "ema_align", "trend")])
    qqq = fuse(
        [
            _cand("QQQ", "put", "orb", "level", conv=1.2),
            _cand("QQQ", "put", "ema_align", "trend", conv=1.0),
            _cand("QQQ", "put", "gap_and_go", "gap", conv=1.1),
        ]
    )
    best = pick_strongest([spy, qqq])
    assert best is not None
    assert best.ticker == "QQQ"
    assert best.direction == "put"


def test_setups_fire_orb_and_trend_on_bullish_open() -> None:
    cands = setups_for("SPY", bullish_open())
    names = {c.strategy for c in cands}
    assert "orb" in names
    assert "ema_align" in names
    fused = fuse(cands)
    assert fused is not None
    assert fused.direction == "call"


def test_scan_fires_only_in_hunt(tmp_path) -> None:
    settings = make_settings(tmp_path)
    bars = {"SPY": bullish_open()}
    weekend = datetime(2026, 8, 30, 21, 0, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: weekend),
        tickers=["SPY"],
        bars_by_ticker=bars,
    )
    assert tape["phase"] == "weekend"
    assert tape["strongest"] is None

    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker=bars,
    )
    assert tape["phase"] == "hunt"
    assert tape["strongest"] is None
    assert tape["signals"] == []
    spy = next(r for r in tape["rows"] if r["ticker"] == "SPY")
    assert spy["fused"]["direction"] == "call"
    assert any(q.get("ticker") == "SPY" for q in tape["queue"])


def test_scan_publishes_every_confluence(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY", "QQQ"],
        bars_by_ticker={"SPY": bullish_open("SPY"), "QQQ": bullish_open("QQQ")},
    )
    assert tape["signals"] == []
    queued = {s["ticker"] for s in tape["queue"]}
    assert {"SPY", "QQQ"} <= queued
    assert tape["scanned"] == ["SPY", "QQQ"]


def test_hold_when_red_but_vwap_intact() -> None:
    advice = advise(
        is_call=True,
        entry=1.32,
        mark=0.90,
        underlying=771.0,
        vwap=769.0,
        ema9=770.5,
        ema9_prev=770.0,
    )
    assert advice.action == "HOLD"
    assert "intact" in advice.headline.lower() or "thesis" in advice.headline.lower()


def test_plan_stop_cuts_0dte_loser() -> None:
    advice = advise(is_call=True, entry=1.32, mark=0.90, is_0dte=True, days_to_expiry=0)
    assert advice.action == "HARD_SELL"
    assert "plan stop" in advice.headline.lower()


def test_underlying_stop_is_failed_prediction() -> None:
    advice = advise(
        is_call=True,
        entry=1.32,
        mark=1.20,
        underlying=768.0,
        vwap=769.0,
        ema9=769.5,
        ema9_prev=769.2,
        underlying_stop=769.5,
        is_0dte=True,
        days_to_expiry=0,
        plan_stop_pct=25,
    )
    assert advice.action == "HARD_SELL"
    assert "through sl" in advice.headline.lower() or "prediction failed" in advice.headline.lower()


def test_tape_flip_sells_the_wrong_side() -> None:
    advice = advise(
        is_call=True,
        entry=1.32,
        mark=1.10,
        tape_direction="put",
        days_to_expiry=0,
        is_0dte=True,
        plan_stop_pct=25,
    )
    assert advice.action == "STRONG_SELL"
    advice = advise(
        is_call=True,
        entry=1.32,
        mark=0.50,
        underlying=760.0,
        vwap=770.0,
        ema9=761.0,
        ema9_prev=762.0,
    )
    assert advice.action == "HARD_SELL"


def test_swing_down_without_tape_is_hold() -> None:
    advice = advise(is_call=True, entry=0.09, mark=0.01, days_to_expiry=19)
    assert advice.action == "HOLD"


def test_winner_that_ran_is_not_allowed_to_close_red() -> None:
    """PLTR on 2026-09-11 peaked at +45% and was still held all the way to -15%."""
    advice = advise(
        is_call=False,
        entry=1.56,
        mark=1.55,
        peak_mark=2.27,
        underlying=167.0,
        vwap=166.0,
        ema9=166.5,
        ema9_prev=166.2,
        days_to_expiry=0,
        is_0dte=True,
        plan_stop_pct=25,
    )
    assert advice.action == "TAKE_PROFIT"
    # A +46% run now defends the run rather than scratch, so it banks well above
    # breakeven instead of merely avoiding red.
    assert breakeven_floor(1.56, 2.27) == pytest.approx(22.76)


def test_oversized_contract_is_refused() -> None:
    """One SPX contract at $11.90 risks $297 at a 25% stop — 56% of the loss on 2026-09-11."""
    from pa.open_session.ledger import risk_block

    assert risk_block({"entry": 11.90, "plan_stop_pct": 25.0}) is not None
    assert risk_block({"entry": 2.00, "plan_stop_pct": 25.0}) is None


def test_penny_contract_is_refused() -> None:
    """A 3c option's percentage P&L is bid-ask noise, not a result."""
    from pa.open_session.ledger import risk_block

    assert risk_block({"entry": 0.03, "plan_stop_pct": 25.0}) is not None


def test_open_position_keeps_its_own_price_history() -> None:
    """Endpoints cannot settle an exit question; the path between them can."""
    from pa.open_session.ledger import PATH_MAX_POINTS, _refresh_open_pnl

    row = {"status": "open", "entry": 1.00, "mark": 1.10, "bid": 1.08}
    _refresh_open_pnl(row)
    assert len(row["marks"]) == 1
    assert row["marks"][0][1] == 1.10

    # A quote that has not moved is not a new observation.
    _refresh_open_pnl(row)
    assert len(row["marks"]) == 1
    row["mark"], row["bid"] = 1.25, 1.23
    _refresh_open_pnl(row)
    assert len(row["marks"]) == 2

    # The series is bounded, so a position held all day cannot bloat the book.
    for i in range(PATH_MAX_POINTS + 50):
        row["mark"] = 1.0 + i / 1000.0
        _refresh_open_pnl(row)
    assert len(row["marks"]) == PATH_MAX_POINTS

    # A closed row is final and must not keep collecting quotes.
    closed = {"status": "closed", "entry": 1.00, "mark": 1.10}
    _refresh_open_pnl(closed)
    assert "marks" not in closed


def test_a_dark_quote_feed_is_counted_and_announced(monkeypatch, caplog) -> None:
    """A dead feed and a quiet one both return None; only one is an emergency."""
    import logging

    from pa.open_session import ledger

    monkeypatch.setattr(ledger, "_quote_misses", 0, raising=False)
    row = {"ticker": "SPY", "strike": 660.0, "expiry": "2026-09-18", "direction": "call"}

    monkeypatch.setattr(
        "pa.open_session.contract.quote_detail",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("feed down")),
    )
    with caplog.at_level(logging.ERROR, logger="pa.ledger"):
        for _ in range(ledger.QUOTE_DARK_AFTER):
            ledger._quote_row(dict(row))
    assert ledger.quote_health() == ledger.QUOTE_DARK_AFTER
    assert any("quote feed dark" in r.message for r in caplog.records)

    # A good quote clears the streak.
    monkeypatch.setattr(
        "pa.open_session.contract.quote_detail",
        lambda *a, **k: {"mid": 2.0, "bid": 1.95, "source": "uw", "age": 1.0},
    )
    ledger._quote_row(dict(row))
    assert ledger.quote_health() == 0


def test_tape_block_refuses_bets_against_the_tape() -> None:
    """The one cut that keeps its sign in both halves of the sample."""
    from pa.open_session.scan import tape_block

    assert tape_block("call", -0.12) is not None
    assert tape_block("put", 0.12) is not None
    # Agreeing with the tape is allowed.
    assert tape_block("call", 0.12) is None
    assert tape_block("put", -0.12) is None
    # Too early in a session to have a prior 15 minutes is not a disagreement.
    assert tape_block("call", None) is None


def test_mom15_is_computed_once_there_are_enough_bars() -> None:
    from datetime import datetime, timedelta

    from pa.domain.models import Bar, Timeframe
    from pa.open_session.levels import compute_levels

    start = datetime(2026, 9, 15, 9, 30, tzinfo=ET)

    def series(step: float) -> list[Bar]:
        return [
            Bar(ticker="SPY", ts=start + timedelta(minutes=i), open=100.0 + i * step,
                high=100.2 + i * step, low=99.9 + i * step, close=100.1 + i * step,
                volume=1000.0, timeframe=Timeframe.M1)
            for i in range(40)
        ]

    rising = series(0.1)
    assert compute_levels("SPY", rising).mom15 > 0
    falling = series(-0.1)
    assert compute_levels("SPY", falling).mom15 < 0
    # Under 16 bars there is no prior 15 minutes to read.
    assert compute_levels("SPY", rising[:10]).mom15 is None


def test_macd_refuses_signals_it_disagrees_with() -> None:
    """Calls need the histogram building up, puts need it building down."""
    from pa.open_session.scan import macd_block

    assert macd_block("call", 0.12) is None
    assert macd_block("put", -0.12) is None
    assert macd_block("call", -0.12) is not None
    assert macd_block("put", 0.12) is not None
    # Too early in a session to have one is not the same as disagreeing.
    assert macd_block("call", None) is None
    assert macd_block("put", None) is None


def test_macd_is_computed_once_there_are_enough_bars() -> None:
    from datetime import datetime, timedelta

    from pa.domain.models import Bar, Timeframe
    from pa.open_session.levels import compute_levels

    start = datetime(2026, 9, 15, 9, 30, tzinfo=ET)
    rising = [
        Bar(ticker="SPY", ts=start + timedelta(minutes=i), open=100.0 + i * 0.1,
            high=100.2 + i * 0.1, low=99.9 + i * 0.1, close=100.1 + i * 0.1,
            volume=1000.0, timeframe=Timeframe.M1)
        for i in range(60)
    ]
    assert compute_levels("SPY", rising).macd_hist is not None
    assert compute_levels("SPY", rising[:10]).macd_hist is None


def test_day_stops_trading_once_the_loss_stop_is_hit() -> None:
    """A losing session should stop opening positions, not keep feeding the tape."""
    from pa.open_session.ledger import MAX_DAILY_LOSS, daily_loss_block

    from pa.open_session.ledger import MAX_RISK_PER_TRADE

    # The stop is two full stop-outs, so it has to be stated in those units:
    # sizing made a stop-out worth the whole risk budget rather than ~$50.
    day = "2026-09-11"
    hurt = [
        {"status": "closed", "opened_at": f"{day}T09:55:00-04:00",
         "pnl_dollars": -MAX_RISK_PER_TRADE},
        {"status": "closed", "opened_at": f"{day}T10:20:00-04:00",
         "pnl_dollars": -MAX_RISK_PER_TRADE},
    ]
    assert daily_loss_block(hurt, day) is not None
    # Open rows have no realised P&L yet, so they must not trip the stop.
    assert daily_loss_block([{"status": "open", "opened_at": f"{day}T09:55:00-04:00"}], day) is None
    # Yesterday's damage does not follow us into a new session.
    assert daily_loss_block(hurt, "2026-09-12") is None
    ok = [{"status": "closed", "opened_at": f"{day}T09:55:00-04:00",
           "pnl_dollars": -(MAX_DAILY_LOSS - 50.0)}]
    assert daily_loss_block(ok, day) is None


def test_premium_band_holds_both_ends() -> None:
    """The floor sits at the spread cliff, which measured at $0.50, not $1.00.

    Median round-trip cost on live-feed rows is 11.8% under $0.50 but only 3.8%
    from $0.50-$1.00 -- tighter than the $2.00-$3.50 band the desk already
    trades. A $1.00 floor priced out the whole liquid end of the watchlist: an
    ATM 0DTE SPY contract is under $1.00 at every hour of the session.
    """
    from pa.open_session.ledger import MIN_PREMIUM, risk_block

    assert risk_block({"entry": 0.31, "plan_stop_pct": 25.0}) is not None
    for ok in (MIN_PREMIUM, 0.92, 2.00, 3.50, 6.50):
        assert risk_block({"entry": ok, "plan_stop_pct": 25.0}) is None


def test_a_dear_contract_is_sized_down_rather_than_refused() -> None:
    """The $3.50 ceiling was standing in for position sizing, and did it badly.

    Because an ATM premium decays with the square root of time left, the ceiling
    did not refuse dear contracts so much as postpone them: the META contract
    that cost $6.50 at 10:03 fell under $3.50 only by 14:30, so the desk could
    only ever buy it after the move was over.
    """
    from pa.open_session.ledger import MAX_RISK_PER_TRADE, contracts_for, lots_for_risk, risk_block

    # The 10:03 META contract is affordable now: it is no longer judged on price.
    assert risk_block({"entry": 6.50, "plan_stop_pct": 25.0}) is None
    assert contracts_for(6.50, 25.0) >= 1

    # The sizing arithmetic equalises risk: a cheap contract needs more lots to
    # reach the same dollar risk, and no premium exceeds the per-trade budget.
    assert lots_for_risk(0.50, 25.0) > lots_for_risk(3.25, 25.0)
    for premium in (0.50, 0.92, 2.00, 3.25, 6.50, 9.00):
        lots = lots_for_risk(premium, 25.0)
        assert lots >= 1
        assert lots * premium * 100.0 * 0.25 <= MAX_RISK_PER_TRADE


def test_sizing_is_switched_off_until_the_edge_is_positive() -> None:
    """Sizing multiplies edge, and measured edge is still negative.

    Replaying the 77 live-feed trades under equal-risk sizing turned -$714 into
    -$3,345 while the win rate moved only 31.5% -> 32.4%, so the lot cap stays
    at 1 and the desk keeps behaving exactly as it did.
    """
    from pa.open_session.ledger import MAX_CONTRACTS, contracts_for

    assert MAX_CONTRACTS == 1
    for premium in (0.50, 0.92, 2.00, 3.25, 6.50):
        assert contracts_for(premium, 25.0) == 1


def test_sizing_still_refuses_what_it_cannot_afford() -> None:
    """SPX is what the old ceiling was really aimed at, and sizing still stops it.

    One SPX contract at $46.66 risks $1,166 against a $225 budget, so there is
    no lot size that fits and the trade is refused on risk rather than on price.
    """
    from pa.open_session.ledger import contracts_for, risk_block

    assert contracts_for(46.66, 25.0) == 0
    assert risk_block({"entry": 46.66, "plan_stop_pct": 25.0}) is not None


def test_position_pnl_counts_every_contract() -> None:
    """Percent is per contract, dollars are for the position."""
    from pa.open_session.ledger import _pnl

    dollars, pct = _pnl(0.50, 0.75, 8)
    assert pct == 50.0
    assert dollars == 200.0

    # Rows written before sizing existed were genuinely one contract each.
    assert _pnl(0.50, 0.75, None) == (25.0, 50.0)


def test_an_atm_spy_zero_dte_contract_is_affordable() -> None:
    """The exact quotes the desk refused on 2026-09-21 must now pass.

    The engine printed 12 SPY signals that day while SPY ran +1.06%, and booked
    none: every contract it priced was rejected for costing too little.
    """
    from pa.open_session.ledger import risk_block

    for quoted in (0.50, 0.60, 0.72, 0.87, 0.92, 0.97):
        assert risk_block({"entry": quoted, "plan_stop_pct": 25.0}) is None


def test_fourth_position_on_one_side_is_refused() -> None:
    from pa.open_session.ledger import crowding_block

    live = [
        {"status": "open", "direction": "call", "ticker": t, "opened_at": "2026-09-11T09:55:00-04:00"}
        for t in ("AAPL", "AMZN", "MSFT")
    ]
    sig = {"direction": "call", "ticker": "META"}
    assert crowding_block(sig, live, "2026-09-11") is not None
    # The other side is a different bet and stays available.
    assert crowding_block({"direction": "put", "ticker": "META"}, live, "2026-09-11") is None


def test_second_index_on_one_side_is_refused() -> None:
    """QQQ, DIA and SPX calls at the same time are one trade wearing three tickers."""
    from pa.open_session.ledger import crowding_block

    live = [{"status": "open", "direction": "call", "ticker": "QQQ", "opened_at": "2026-09-11T09:59:00-04:00"}]
    assert crowding_block({"direction": "call", "ticker": "SPX"}, live, "2026-09-11") is not None
    # A single name alongside one index is still allowed.
    assert crowding_block({"direction": "call", "ticker": "AAPL"}, live, "2026-09-11") is None


def test_no_signal_may_flip_the_side_a_name_already_used() -> None:
    """The one-side-per-name rule now applies to everything without exception."""
    from pa.open_session.ledger import other_side_block

    taken = {"SPY": "call"}
    assert other_side_block("SPY", "put", taken) is not None
    assert other_side_block("SPY", "call", taken) is None
    assert other_side_block("QQQ", "put", taken) is None


def test_amzn_style_underlying_stop_cannot_close_a_run_red() -> None:
    """2026-09-14: AMZN closed -13% on underlying_stop, which is checked before
    the floor. peak_mark is the value the desk actually recorded (1.03, a +11.96%
    run), not the larger number visible on the dashboard — the arm has to clear
    the observed peak, so this pins the real one."""
    out = advise(
        is_call=False,
        entry=0.92,
        mark=0.92,
        peak_mark=1.03,
        underlying=232.50,
        underlying_stop=232.00,
        minutes_held=6.0,
    )

    assert out.action == "TAKE_PROFIT"
    assert "through SL" not in out.headline


def test_iwm_style_run_now_arms_the_floor() -> None:
    """IWM peaked +21.7% and closed -15.2%: under the old 25% arm it never armed.

    It now defends half the run rather than scratch. The flat +3% floor used to
    apply to everything under a 30% run, so a trade like this handed back nine
    tenths of what it made and still counted as "protected".
    """
    floor = breakeven_floor(0.92, 1.12)
    assert floor is not None
    assert floor > BREAKEVEN_FLOOR_PCT
    assert floor == pytest.approx(21.7 * RATCHET_KEEP, abs=0.1)

    out = advise(is_call=False, entry=0.92, mark=0.90, peak_mark=1.12, minutes_held=10.0)
    assert out.action == "TAKE_PROFIT"


def test_the_floor_has_no_cliff_in_it() -> None:
    """Nothing about a trade changes at exactly a 30% run.

    The floor used to jump from +3% to +15% across that boundary, so two trades
    a hundredth of a point apart were protected twelve points differently.
    """
    just_under = breakeven_floor(1.00, 1.299)
    just_over = breakeven_floor(1.00, 1.300)
    assert just_under is not None and just_over is not None
    assert abs(just_over - just_under) < 0.5

    # And the floor rises monotonically with the run it is defending.
    floors = [breakeven_floor(1.00, 1.0 + run / 100.0) for run in (10, 15, 20, 25, 30, 45, 60)]
    assert all(a <= b for a, b in zip(floors, floors[1:]))


def test_floor_judges_on_the_bid_because_that_is_what_it_fills_at() -> None:
    """2026-09-14 AVGO: mid said +3.7% so the floor held, the bid was -0.6% and
    it closed red. The floor has to read the price we actually sell into."""
    armed = {"is_call": True, "entry": 1.55, "peak_mark": 1.75, "minutes_held": 20.0}

    # Mid still above the floor, but the bid is not: sell now, not later.
    assert advise(mark=1.61, bid=1.56, **armed).action == "TAKE_PROFIT"
    # Bid comfortably clear of the floor: nothing to do.
    assert advise(mark=1.72, bid=1.70, **armed).action != "TAKE_PROFIT"


def test_a_trade_that_never_ran_is_left_to_the_ordinary_stops() -> None:
    # Peaked +5%, which is inside the spread, so no floor and the stop still works.
    assert breakeven_floor(1.00, 1.05) is None

    out = advise(
        is_call=True,
        entry=1.00,
        mark=0.70,
        peak_mark=1.05,
        underlying=99.0,
        underlying_stop=100.0,
        minutes_held=6.0,
    )
    assert out.action == "HARD_SELL"


def test_a_big_run_defends_the_run_not_scratch() -> None:
    """PLTR peaked +45% and closed -15%. A breakeven floor alone would have
    scratched it at +3%; the ratchet keeps half the run."""
    assert breakeven_floor(1.00, 1.46) == pytest.approx(23.0)

    out = advise(is_call=True, entry=1.00, mark=1.20, peak_mark=1.46, minutes_held=20.0)
    assert out.action == "TAKE_PROFIT"


def test_an_armed_trade_still_above_its_floor_is_left_alone() -> None:
    out = advise(
        is_call=True,
        entry=1.00,
        mark=1.15,
        peak_mark=1.20,
        underlying=101.0,
        vwap=100.0,
        ema9=100.5,
        ema9_prev=100.2,
        minutes_held=10.0,
    )

    assert out.action in {"HOLD", "SCALE_OUT"}


def test_red_take_profit_is_relabelled() -> None:
    """The advisor decides on the mid but fills at the bid, so a wide 0DTE
    spread can turn a 'take profit' red. Recording that as take_profit both
    misreports the day and trains the policy that a loss was a good exit."""
    from pa.open_session.ledger import _close

    row = {"ticker": "PLTR", "entry": 1.56, "verdict": "WATCH", "status": "open"}
    _close(row, datetime(2026, 9, 11, 11, 23, tzinfo=ET), exit_px=1.32, reason="take_profit")
    assert row["reason"] == "trail_stop"
    assert row["pnl_pct"] < 0


def test_green_take_profit_keeps_its_label() -> None:
    from pa.open_session.ledger import _close

    row = {"ticker": "PLTR", "entry": 1.56, "verdict": "WATCH", "status": "open"}
    _close(row, datetime(2026, 9, 11, 11, 23, tzinfo=ET), exit_px=2.10, reason="take_profit")
    assert row["reason"] == "take_profit"
    assert row["pnl_pct"] > 0


def test_small_run_does_not_arm_the_breakeven_floor() -> None:
    """A trade that only ticked up must still be free to breathe."""
    advice = advise(
        is_call=True,
        entry=1.00,
        mark=0.99,
        peak_mark=1.05,
        underlying=771.0,
        vwap=769.0,
        ema9=770.5,
        ema9_prev=770.0,
        days_to_expiry=0,
        is_0dte=True,
        plan_stop_pct=25,
    )
    assert advice.action != "TAKE_PROFIT"


def test_babysit_seeded_spy_call(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_watch(
        settings.data_dir,
        {"ticker": "SPY", "right": "call", "strike": 770, "expiry": "2026-08-31", "entry": 1.32, "mark": 1.20, "plan_stop_pct": 25},
    )
    levels = {"SPY": {"last": 771.0, "vwap": 769.5, "ema9": 770.8, "ema9_prev": 770.4}}
    payload = review_positions(
        settings,
        levels_by_ticker=levels,
        today=datetime(2026, 8, 31).date(),
    )
    assert payload["positions"]
    row = payload["positions"][0]
    assert row["action"] == "HOLD"
    assert row["ticker"] == "SPY"


def test_buy_sell_wording() -> None:
    from datetime import date

    from pa.open_session.contract import format_buy, format_sell

    buy = format_buy("IWM", 293, "put", date(2026, 8, 31), 1.45)
    sell = format_sell("IWM", 293, "put", 2.90, 100)
    assert buy == "Buy IWM 293 Put Exp 8/31 for $1.45"
    assert sell == "Sell IWM 293 Put for $2.90 Take Profit 100%"
    from pa.open_session.contract import format_exit_sell

    assert (
        format_exit_sell(
            "DIA", 530, "put", date(2026, 9, 4), 1.10, pnl_pct=55, pnl_dollars=39.0, reason="take_profit"
        )
        == "Sell DIA 530 Put Exp 9/4 for $1.10 Take Profit +55% (+$39.00)"
    )
    # A losing exit carries its P/L too, instead of a bare "Stop".
    assert (
        format_exit_sell(
            "AAPL", 315, "call", date(2026, 8, 31), 0.80, pnl_pct=-29, pnl_dollars=-32.0, reason="hard_stop"
        )
        == "Sell AAPL 315 Call Exp 8/31 for $0.80 Stop -29% (-$32.00)"
    )
    assert (
        format_exit_sell("AAPL", 315, "call", date(2026, 8, 31), 0.80, reason="hard_stop")
        == "Sell AAPL 315 Call Exp 8/31 for $0.80 Stop"
    )


def _pltr_sig(**kwargs) -> dict:
    row = {
        "ticker": "PLTR",
        "direction": "put",
        "strike": 185.0,
        "opt": "P",
        "expiry": "2026-08-31",
        "entry": 1.45,
        "target": 2.24,
        "take_profit_pct": 55.0,
        "premium_source": "model",
        "conviction": 2.2,
        "stop": 187.0,
        "trigger": 184.7,
        "plan_stop_pct": 25.0,
        "window": "after_90",
    }
    row.update(kwargs)
    return row


def test_ledger_signal_matches_columns_after_stale_quote(tmp_path) -> None:
    from pa.open_session.ledger import save_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    save_book(
        settings.data_dir,
        {
            "trades": [
                {
                    "id": "PLTR-put-2026-08-31",
                    "status": "open",
                    "opened_at": now.isoformat(),
                    "ticker": "PLTR",
                    "direction": "put",
                    "strike": 185.0,
                    "opt": "P",
                    "expiry": "2026-08-31",
                    "entry": 1.45,
                    "target": 2.24,
                    "take_profit_pct": 55.0,
                    "text_buy": "Buy PLTR 185 Put Exp 9/4 for $3.70",
                    "text_sell": "Sell PLTR 185 Put for $5.72 Take Profit 55%",
                    "premium_source": "model",
                }
            ]
        },
    )
    book = sync_book(
        settings.data_dir,
        [
            _pltr_sig(
                expiry="2026-09-04",
                entry=3.70,
                target=5.72,
                premium_source="chain",
                text_buy="Buy PLTR 185 Put Exp 9/4 for $3.70",
                text_sell="Sell PLTR 185 Put for $5.72 Take Profit 55%",
            )
        ],
        now,
    )
    row = next(t for t in book["trades"] if t["id"] == "PLTR-put-2026-08-31")
    assert row["expiry"] == "2026-08-31"
    assert row["entry"] == 1.45
    assert row["text_buy"] == "Buy PLTR 185 Put Exp 8/31 for $1.45"
    assert row["text_sell"] == "Sell PLTR 185 Put for $2.24 Take Profit 55%"


def test_ledger_fills_missing_contract_then_freezes(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(strike=None, expiry=None, entry=None, target=None)], now)
    row = load_book(settings.data_dir)["trades"][0]
    assert row.get("entry") is None
    assert row.get("expiry") is None

    sync_book(
        settings.data_dir,
        [_pltr_sig(expiry="2026-09-04", entry=3.70, target=5.72, premium_source="chain")],
        now,
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["expiry"] == "2026-09-04"
    assert row["entry"] == 3.70
    assert row["text_buy"] == "Buy PLTR 185 Put Exp 9/4 for $3.70"

    sync_book(settings.data_dir, [_pltr_sig(expiry="2026-08-31", entry=1.45, target=2.24)], now)
    row = load_book(settings.data_dir)["trades"][0]
    assert row["expiry"] == "2026-09-04"
    assert row["entry"] == 3.70
    assert row["text_buy"] == "Buy PLTR 185 Put Exp 9/4 for $3.70"


def test_ledger_records_exit_and_pnl(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    later = datetime(2026, 8, 31, 10, 13, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.90, target=4.50, stop=187.0, trigger=184.7)], now)
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "open"
    assert row["entry"] == 2.90
    assert row.get("exit") is None

    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=2.84, target=4.50, stop=187.0, trigger=184.7)],
        later,
        levels_by_ticker={"PLTR": {"last": 188.0, "vwap": 185.0, "ema9": 186.0, "ema9_prev": 185.5}},
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "closed"
    assert row["entry"] == 2.90
    assert row["exit"] == 2.84
    assert row["contracts"] == 1       # sizing stays at one lot while edge is negative
    assert row["pnl_dollars"] == -6.0  # -$0.06 a share on a single contract
    assert row["pnl_pct"] == -2.1
    assert row["verdict"] == "TAKE"
    assert row["prediction"] == "FAIL"


def test_ledger_ignores_trigger_in_first_minutes(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    soon = datetime(2026, 8, 31, 10, 10, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.90, target=4.50, stop=187.0, trigger=184.7)], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=2.84, target=4.50, stop=187.0, trigger=184.7)],
        soon,
        levels_by_ticker={"PLTR": {"last": 188.0, "vwap": 185.0, "ema9": 186.0, "ema9_prev": 185.5}},
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "open"
    assert row["mark"] == 2.84
    assert row["contracts"] == 1       # sizing stays at one lot while edge is negative
    assert row["pnl_dollars"] == -6.0  # -$0.06 a share on a single contract
    assert row["pnl_pct"] == -2.1


def test_does_not_open_other_side_same_day(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    later = datetime(2026, 8, 31, 10, 40, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(ticker="AVGO", direction="call", opt="C")], now)
    sync_book(settings.data_dir, [_pltr_sig(ticker="AVGO", direction="put", opt="P")], later)
    avgo = [t for t in load_book(settings.data_dir)["trades"] if t.get("ticker") == "AVGO"]
    assert len(avgo) == 1
    assert avgo[0]["direction"] == "call"


def test_failed_breakout_waits_15_minutes(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    ten = datetime(2026, 8, 31, 10, 18, tzinfo=ET)
    sixteen = datetime(2026, 8, 31, 10, 24, tzinfo=ET)
    levels = {"PLTR": {"last": 186.0, "vwap": 185.0, "ema9": 186.0, "ema9_prev": 185.5}}
    sync_book(settings.data_dir, [_pltr_sig(entry=2.90, stop=187.0, trigger=184.7)], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=2.80, stop=187.0, trigger=184.7)],
        ten,
        levels_by_ticker=levels,
    )
    assert load_book(settings.data_dir)["trades"][0]["status"] == "open"
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=2.80, stop=187.0, trigger=184.7)],
        sixteen,
        levels_by_ticker=levels,
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "closed"
    assert row["reason"] == "failed_breakout"


def test_quote_death_is_rejected_not_booked(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    soon = datetime(2026, 8, 31, 10, 9, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.00, target=3.10)], now)
    sync_book(settings.data_dir, [_pltr_sig(entry=0.02, target=3.10)], soon)
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "open"
    assert row["mark"] == 2.00
    assert row.get("quote_reject") is True
    assert row.get("exit") is None


def test_plan_stop_fires_as_soon_as_it_is_hit(tmp_path) -> None:
    """A plan stop is a risk limit, so it may not be deferred by a hold timer.

    This used to wait PLAN_HOLD_MINUTES (15). On 2026-09-11 that let every one
    of eight stop-outs run from -25% to between -30% and -65% before selling.
    """
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    past_grace = datetime(2026, 8, 31, 10, 10, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.00, plan_stop_pct=25.0, stop=None, trigger=None)], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=1.40, plan_stop_pct=25.0, stop=None, trigger=None)],
        past_grace,
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "closed"
    assert row["reason"] == "hard_stop"


def test_plan_stop_respects_the_one_minute_grace(tmp_path) -> None:
    """Still ignore the very first quote after a fill, so a bad print cannot stop us out."""
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    within_grace = datetime(2026, 8, 31, 10, 8, 30, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.00, plan_stop_pct=25.0, stop=None, trigger=None)], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=1.40, plan_stop_pct=25.0, stop=None, trigger=None)],
        within_grace,
    )
    assert load_book(settings.data_dir)["trades"][0]["status"] == "open"


def test_scan_blocks_other_side_already_taken(tmp_path) -> None:
    from pa.open_session.ledger import save_book

    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    save_book(
        settings.data_dir,
        {
            "trades": [
                {
                    "id": "QQQ-put-2026-08-31",
                    "status": "open",
                    "opened_at": hunt.isoformat(),
                    "ticker": "QQQ",
                    "direction": "put",
                    "entry": 1.0,
                    "verdict": "TAKE",
                }
            ]
        },
    )
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["QQQ"],
        bars_by_ticker={"QQQ": bullish_open("QQQ")},
    )
    qqq = next(r for r in tape["rows"] if r["ticker"] == "QQQ")
    assert qqq["fused"]["vetoed"] is True
    assert "already put" in (qqq["fused"]["thesis"] or "").lower()
    assert "QQQ" not in {s["ticker"] for s in tape["signals"]}


def test_ledger_mark_ignores_different_contract_quote(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=1.45, expiry="2026-08-31")], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=3.70, expiry="2026-09-04", premium_source="chain")],
        now,
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["entry"] == 1.45
    assert row["expiry"] == "2026-08-31"
    assert row["mark"] == 1.45


def test_incomplete_close_gets_exit_and_pnl(tmp_path) -> None:
    from pa.open_session.ledger import repair_book, save_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    save_book(
        settings.data_dir,
        {
            "trades": [
                {
                    "id": "QQQ-put-2026-08-31",
                    "status": "closed",
                    "opened_at": now.isoformat(),
                    "closed_at": now.isoformat(),
                    "ticker": "QQQ",
                    "direction": "put",
                    "strike": 715.0,
                    "opt": "P",
                    "expiry": "2026-08-31",
                    "entry": 1.61,
                    "exit": 1.21,
                    "plan_stop_pct": 25.0,
                    "prediction": "FAIL",
                    "reason": "failed_breakout",
                }
            ]
        },
    )
    row = repair_book(settings.data_dir)["trades"][0]
    assert row["status"] == "open"
    assert row.get("exit") is None
    assert row.get("closed_at") is None


def test_close_without_price_stays_open(tmp_path) -> None:
    from pa.open_session.ledger import _close

    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    row = {"status": "open", "entry": 1.61, "exit": None, "closed_at": None}
    _close(row, now, exit_px=None, reason="failed_breakout", prediction="FAIL")
    assert row["status"] == "open"
    assert row.get("closed_at") is None


def test_save_book_does_not_drop_existing_trades(tmp_path) -> None:
    from pa.open_session.ledger import load_book, save_book

    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                {"id": "AAPL-put-2026-08-31", "ticker": "AAPL", "status": "open", "entry": 0.41},
                {"id": "QQQ-put-2026-08-31", "ticker": "QQQ", "status": "closed", "entry": 1.61, "exit": 1.65},
            ]
        },
    )
    save_book(
        settings.data_dir,
        {"trades": [{"id": "XOM-put-2026-08-31", "ticker": "XOM", "status": "open", "entry": 2.12}]},
    )
    ids = {t["id"] for t in load_book(settings.data_dir)["trades"]}
    assert ids == {"AAPL-put-2026-08-31", "QQQ-put-2026-08-31", "XOM-put-2026-08-31"}


def test_take_green_is_pass_even_when_chart_exits(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    later = datetime(2026, 8, 31, 10, 24, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.90, target=4.50, stop=187.0, trigger=184.7)], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=3.20, target=4.50, stop=187.0, trigger=184.7)],
        later,
        levels_by_ticker={"PLTR": {"last": 186.0, "vwap": 185.0, "ema9": 186.0, "ema9_prev": 185.5}},
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "closed"
    assert row["pnl_pct"] > 0
    assert row["verdict"] == "TAKE"
    assert row["prediction"] == "PASS"


def test_repair_regrades_take_profit_as_pass(tmp_path) -> None:
    from pa.open_session.ledger import repair_book, save_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 11, 10, tzinfo=ET)
    save_book(
        settings.data_dir,
        {
            "trades": [
                {
                    "id": "AMZN-put-2026-08-31",
                    "status": "closed",
                    "opened_at": now.isoformat(),
                    "closed_at": now.isoformat(),
                    "ticker": "AMZN",
                    "direction": "put",
                    "entry": 0.46,
                    "exit": 0.57,
                    "mark": 0.57,
                    "pnl_dollars": 11.0,
                    "pnl_pct": 23.9,
                    "verdict": "TAKE",
                    "prediction": "FAIL",
                    "reason": "failed_breakout",
                }
            ]
        },
    )
    row = repair_book(settings.data_dir)["trades"][0]
    assert row["status"] == "closed"
    assert row["prediction"] == "PASS"


def _ohlc(ticker: str, ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        ticker=ticker,
        ts=ts,
        open=o,
        high=max(h, o, c),
        low=min(l, o, c),
        close=c,
        volume=1_000_000,
        timeframe=Timeframe.M1,
    )


def dump_after_spike(ticker: str = "SPY") -> list[Bar]:
    """Quiet open, rip into the upper band, then three full-ATR red bars — the put we used to miss."""
    pm = _grind(ticker, DAY.replace(hour=9, minute=0), 20, 99.0, step=0.01, high_pad=0.08)
    orb = []
    start = DAY.replace(hour=9, minute=30)
    for i in range(15):
        px = 100.0 + 0.01 * (i % 3)
        ts = start + timedelta(minutes=i)
        orb.append(_bar(ticker, ts, px, high=px + 0.12, low=px - 0.08))
    chop = []
    start = DAY.replace(hour=9, minute=45)
    for i in range(40):
        px = 100.2 + 0.02 * (i % 5)
        ts = start + timedelta(minutes=i)
        chop.append(_bar(ticker, ts, px, high=px + 0.08, low=px - 0.06))
    spike_t = DAY.replace(hour=10, minute=25)
    px = chop[-1].close
    spike = []
    for i, jump in enumerate((0.9, 1.1, 1.3)):
        o, c = px, px + jump
        spike.append(_ohlc(ticker, spike_t + timedelta(minutes=i), o, c + 0.15, o - 0.05, c))
        px = c
    dump = []
    for i, drop in enumerate((1.6, 1.8, 2.2)):
        o, c = px, px - drop
        dump.append(_ohlc(ticker, spike_t + timedelta(minutes=3 + i), o, o + 0.08, c - 0.12, c))
        px = c
    return pm + orb + chop + spike + dump


def test_momentum_burst_may_stand_alone() -> None:
    fused = fuse([_cand("SPY", "put", "momentum_burst", "momentum", conv=1.2)])
    assert fused is not None
    assert fused.direction == "put"


def test_impulse_one_close_counts_as_orb() -> None:
    ticker = "SPY"
    pm = _grind(ticker, DAY.replace(hour=9, minute=0), 20, 99.0, step=0.01, high_pad=0.08)
    orb = _grind(ticker, DAY.replace(hour=9, minute=30), 15, 100.4, step=0.0, high_pad=0.15)
    dump = _ohlc(ticker, DAY.replace(hour=9, minute=45), 100.45, 100.5, 99.0, 99.05)
    cands = setups_for(ticker, pm + orb + [dump])
    names = {c.strategy for c in cands}
    assert "orb" in names
    put = next(c for c in cands if c.strategy == "orb")
    assert put.direction == "put"
    assert "impulse" in put.thesis


def test_dump_after_upper_band_is_a_put() -> None:
    cands = setups_for("SPY", dump_after_spike())
    names = {c.strategy for c in cands if c.direction == "put"}
    assert "bb_rejection" in names or "momentum_burst" in names
    fused = fuse(cands)
    assert fused is not None
    assert fused.vetoed is False
    assert fused.direction == "put"


def test_scan_takes_the_dump_put(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": dump_after_spike("SPY")},
    )
    assert tape["phase"] == "hunt"
    assert tape["strongest"] is not None
    assert tape["strongest"]["direction"] == "put"
    assert tape["strongest"]["verdict"] == "TAKE"
    assert tape["strongest"]["score"] >= 70


def test_scan_no_longer_drops_spy_in_favour_of_spx(tmp_path) -> None:
    """SPX used to win this tie, and then risk_block refused it for costing ~$50
    a contract — so the pair produced no trade at all while SPY alone was +$266.
    SPY has to survive; holding both is crowding_block's call, not the scan's."""
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY", "SPX"],
        bars_by_ticker={"SPY": dump_after_spike("SPY"), "SPX": dump_after_spike("SPX")},
    )
    names = {s["ticker"] for s in tape["signals"]}
    assert "SPY" in names


def test_yf_maps_spx_to_gspc() -> None:
    from pa.open_session.bars import yf_symbol

    assert yf_symbol("SPX") == "^GSPC"
    assert yf_symbol("SPY") == "SPY"


def test_opposing_sides_cancel_with_no_exemption() -> None:
    """orb_fade used to override this veto. Nothing does any more."""
    fused = fuse(
        [
            _cand("SPY", "call", "ema_align", "trend"),
            _cand("SPY", "put", "bb_rejection", "mean_rev"),
        ]
    )

    assert fused is not None
    assert fused.vetoed
    assert fused.veto_reason == "opposing"


def test_no_strategy_may_bypass_the_opposing_veto() -> None:
    """The removed exemption, pinned so it cannot quietly return.

    orb_fade carried three bypasses of rules everything else obeyed, justified
    by an R-multiple study run before the cost bar was known. Re-measured it
    averaged +0.0224% in the early half of the sample and -0.0589% in the late
    half, so the strategy and all three exemptions came out together.
    """
    from pa.open_session.setups import FAMILY, SOLO_STRATEGIES

    assert "orb_fade" not in FAMILY
    assert "orb_fade" not in SOLO_STRATEGIES
    for ticker in ("SPY", "TSLA", "AAPL"):
        assert "orb_fade" not in playbook_for(ticker, elapsed=20.0).allow
