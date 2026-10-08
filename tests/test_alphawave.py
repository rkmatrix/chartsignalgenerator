"""AlphaWave is the chart indicator, not the old setup stack."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pa.domain.models import Bar
from pa.open_session.alphawave import evaluate, resample_5m

ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, price: float, *, volume: float = 1_000.0, span: float = 0.05) -> Bar:
    return Bar(
        ticker="TSLA",
        ts=ts,
        open=price,
        high=price + span,
        low=price - span,
        close=price + span,
        volume=volume,
    )


def _climb(n: int = 70, start: datetime | None = None) -> list[Bar]:
    """A steady climb on regular-session 5m bars, rolling into the next day."""
    ts = start or datetime(2026, 9, 23, 9, 30, tzinfo=ET)
    price = 100.0
    bars = []
    while len(bars) < n:
        if ts.time() >= datetime(2000, 1, 1, 16, 0).time():
            ts = (ts + timedelta(days=1)).replace(hour=9, minute=30)
            continue
        bars.append(_bar(ts, price))
        price = bars[-1].close
        ts = ts + timedelta(minutes=5)
    return bars


def test_premarket_bars_never_reach_the_indicator() -> None:
    """2026-09-30: the 09:25 premarket bar, volume 0, fired six entries at 09:30:06."""
    day = datetime(2026, 9, 30, 4, 0, tzinfo=ET)
    pre = [_bar(day + timedelta(minutes=5 * i), 100.0 + i * 0.1, volume=0) for i in range(66)]
    assert pre[-1].ts.hour == 9 and pre[-1].ts.minute == 25
    assert resample_5m(pre, datetime(2026, 9, 30, 9, 30, 6, tzinfo=ET)) == []
    assert evaluate(pre, datetime(2026, 9, 30, 9, 30, 6, tzinfo=ET)).event is None


def test_the_bar_still_forming_is_not_a_signal() -> None:
    bars = _climb(3)
    now = bars[-1].ts + timedelta(minutes=1)
    closed = resample_5m(bars, now)
    assert len(closed) == 2
    assert closed[-1].ts == bars[1].ts


def _until_call(bars: list[Bar]) -> tuple[list[Bar], datetime]:
    """The prefix whose last closed bar is the first CALL."""
    for k in range(50, len(bars) + 1):
        now = bars[k - 1].ts + timedelta(minutes=5)
        result = evaluate(bars[:k], now)
        if result.event is not None and result.event.kind == "CALL":
            return bars[:k], now
    raise AssertionError("climb never printed a CALL")


def test_a_rising_tape_prints_one_call_and_does_not_repeat_it() -> None:
    bars = _climb(80)
    called, now = _until_call(bars)
    result = evaluate(called, now)
    assert result.event is not None
    assert result.event.kind == "CALL"
    assert result.event.engine == "pullback"
    assert result.event.stop is not None and result.event.stop < result.event.price
    assert result.event.tp1 is not None and result.event.tp1 > result.event.price
    assert result.position == "CALL"

    # The next bar of the same run is not a second buy.
    nxt = called[-1].ts + timedelta(minutes=5)
    followed = called + [_bar(nxt, called[-1].close, volume=1_000)]
    later = evaluate(followed, nxt + timedelta(minutes=5))
    assert later.event is None
    assert later.position == "CALL"


def test_losing_the_9_ema_takes_profit_on_the_call() -> None:
    bars = _climb(80)
    called, _now = _until_call(bars)
    last = called[-1].close
    ts = called[-1].ts + timedelta(minutes=5)
    drop = last - 0.35
    followed = called + [
        Bar(
            ticker="TSLA",
            ts=ts,
            open=last,
            high=last,
            low=drop,
            close=drop,
            volume=1_000,
        )
    ]
    result = evaluate(followed, ts + timedelta(minutes=5))
    assert result.event is not None
    assert result.event.kind == "TP_CALL"
    assert result.position == "FLAT"


def test_an_opening_spread_is_refused() -> None:
    """AMZN 2026-09-30 paid 0.75 against a 0.37 bid and was stopped inside a minute."""
    from pa.open_session.ledger import risk_block

    assert risk_block({"entry": 0.75, "entry_bid": 0.37, "entry_ask": 0.75}) is not None
    assert risk_block({"entry": 1.54, "entry_bid": 1.48, "entry_ask": 1.54}) is None
    # No quote detail at all is not a reason to refuse.
    assert risk_block({"entry": 1.54}) is None


def test_a_name_that_lost_today_is_not_re_entered() -> None:
    from pa.open_session.ledger import lost_today_block

    day = "2026-09-30"
    trades = [
        {"ticker": "NFLX", "status": "closed", "opened_at": f"{day}T09:30:06-04:00", "pnl_dollars": -31.0},
        {"ticker": "MSFT", "status": "closed", "opened_at": f"{day}T09:30:06-04:00", "pnl_dollars": 30.0},
    ]
    assert lost_today_block("NFLX", trades, day) is not None
    assert lost_today_block("MSFT", trades, day) is None
    assert lost_today_block("NFLX", trades, "2026-10-01") is None


def test_the_daily_stop_counts_open_positions_at_the_bid() -> None:
    from pa.open_session.ledger import MAX_DAILY_LOSS, daily_loss_block

    day = "2026-09-30"
    closed = {"status": "closed", "opened_at": f"{day}T09:30:00-04:00", "pnl_dollars": -200.0}
    deep_open = {
        "status": "open", "opened_at": f"{day}T09:55:00-04:00",
        "entry": 5.20, "bid": 3.90, "mark": 4.00, "contracts": 1,
    }
    assert daily_loss_block([closed], day) is None
    assert -200.0 + (3.90 - 5.20) * 100 < -MAX_DAILY_LOSS
    assert daily_loss_block([closed, deep_open], day) is not None


def test_a_red_indicator_exit_is_not_called_take_profit(tmp_path) -> None:
    from pa.open_session.contract import format_exit_sell
    from pa.open_session.ledger import _close

    row = {
        "ticker": "NFLX", "direction": "put", "strike": 70.0, "expiry": "2026-09-30",
        "entry": 0.89, "contracts": 1, "status": "open",
    }
    _close(row, datetime(2026, 9, 30, 9, 50, tzinfo=ET), exit_px=0.74, reason="alphawave_tp")
    assert row["reason"] == "alphawave_exit"
    text = format_exit_sell("NFLX", 70.0, "put", "2026-09-30", 0.74, pnl_pct=-16.9, reason="alphawave_exit")
    assert "Take Profit" not in text


def test_scan_books_the_indicator_and_the_take_profit_closes_it(tmp_path) -> None:
    from pa.clock import MarketClock
    from pa.open_session.ledger import load_book, sync_book
    from pa.open_session.scan import scan_open
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, use_alphawave=True)
    called, now = _until_call(_climb(80))
    clock = MarketClock(now_fn=lambda: now)
    tape = scan_open(
        settings,
        clock,
        tickers=["TSLA"],
        bars_by_ticker={"TSLA": called},
        fetch=False,
    )
    assert tape["signals"], "the closed 5m breakout should be the desk's signal"
    sig = tape["signals"][0]
    assert sig["ticker"] == "TSLA"
    assert sig["direction"] == "call"
    assert "alphawave" in sig["strategies"][0]
    assert sig["verdict"] == "TAKE"

    row = load_book(settings.data_dir)["trades"][0]
    assert row["status"] == "open"
    assert row["signal_bar"]

    # Give the open a premium so the take-profit has a fill, then fire TP.
    row["entry"] = 1.20
    row["mark"] = 1.50
    row["bid"] = 1.48
    from pa.open_session.ledger import save_book

    book = load_book(settings.data_dir)
    book["trades"][0] = row
    save_book(settings.data_dir, book)
    sync_book(
        settings.data_dir,
        [],
        now + timedelta(minutes=5),
        wave_exits=[{"ticker": "TSLA", "direction": "call"}],
        settings=settings,
    )
    closed = load_book(settings.data_dir)["trades"][0]
    assert closed["status"] == "closed"
    assert closed["reason"] == "alphawave_tp"


def test_no_entry_in_the_first_fifteen_minutes(tmp_path, monkeypatch) -> None:
    """Sep 30, Oct 1, Oct 2, Oct 7: every 09:30-09:45 batch closed red."""
    import pa.open_session.scan as scan_mod
    from pa.clock import MarketClock
    from pa.open_session.ledger import load_book
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, use_alphawave=True)
    called, now = _until_call(_climb(80))
    monkeypatch.setattr(scan_mod, "minutes_since_open", lambda _now: 5.0)
    tape = scan_mod.scan_open(
        settings,
        MarketClock(now_fn=lambda: now),
        tickers=["TSLA"],
        bars_by_ticker={"TSLA": called},
        fetch=False,
    )
    assert not tape["signals"]
    assert not load_book(settings.data_dir)["trades"]
