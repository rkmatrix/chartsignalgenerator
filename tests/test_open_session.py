from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pa.babysitter.advise import advise
from pa.babysitter.feed import review_positions, save_watch
from pa.clock import MarketClock
from pa.domain.models import Bar, Timeframe
from pa.open_session.clock import session_phase
from pa.open_session.fuse import fuse, pick_strongest
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
        format_exit_sell("DIA", 530, "put", date(2026, 9, 4), 1.10, pnl_pct=55, reason="take_profit")
        == "Sell DIA 530 Put Exp 9/4 for $1.10 Take Profit 55%"
    )
    assert (
        format_exit_sell("AAPL", 315, "call", date(2026, 8, 31), 0.80, pnl_pct=-29, reason="hard_stop")
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
    assert row["pnl_dollars"] == -6.0
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
    assert row["pnl_dollars"] == -6.0
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


def test_plan_stop_waits_fifteen_minutes(tmp_path) -> None:
    from pa.open_session.ledger import load_book, sync_book

    settings = make_settings(tmp_path)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    soon = datetime(2026, 8, 31, 10, 14, tzinfo=ET)
    later = datetime(2026, 8, 31, 10, 24, tzinfo=ET)
    sync_book(settings.data_dir, [_pltr_sig(entry=2.00, plan_stop_pct=25.0, stop=None, trigger=None)], now)
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=1.40, plan_stop_pct=25.0, stop=None, trigger=None)],
        soon,
    )
    assert load_book(settings.data_dir)["trades"][0]["status"] == "open"
    sync_book(
        settings.data_dir,
        [_pltr_sig(entry=1.40, plan_stop_pct=25.0, stop=None, trigger=None)],
        later,
    )
    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "closed"
    assert row["reason"] == "hard_stop"


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


def test_scan_keeps_spx_over_spy_same_side(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY", "SPX"],
        bars_by_ticker={"SPY": dump_after_spike("SPY"), "SPX": dump_after_spike("SPX")},
    )
    names = {s["ticker"] for s in tape["signals"]}
    assert "SPX" in names
    assert "SPY" not in names


def test_yf_maps_spx_to_gspc() -> None:
    from pa.open_session.bars import yf_symbol

    assert yf_symbol("SPX") == "^GSPC"
    assert yf_symbol("SPY") == "SPY"
