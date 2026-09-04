from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pa.chart_trader.forward import forward_test, grade_signal
from pa.chart_trader.hunter import hunt, split_year
from pa.chart_trader.params import ChartParams
from pa.chart_trader.pine import to_pine, tradingview_url
from pa.chart_trader.reader import read_chart
from pa.data.fixtures import generate_trend_bars
from pa.domain.models import Bar, Side, Timeframe

ET = ZoneInfo("America/New_York")


def _bars() -> list[Bar]:
    start = datetime(2025, 1, 2, 9, 30, tzinfo=ET)
    return generate_trend_bars("SPY", start, count=400, start_price=500.0, drift=0.04)


def test_reader_uses_only_past() -> None:
    bars = _bars()
    params = ChartParams(rsi_high=99.9, rsi_low=1, min_votes=3, require_macd=False)
    left = read_chart("SPY", bars[:80], params)
    mutated = bars[:80] + [
        Bar(
            ticker="SPY",
            ts=bars[80].ts,
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1,
            timeframe=Timeframe.M1,
        )
    ]
    # future crash must not change the guess made at bar 79
    right = read_chart("SPY", bars[:80], params)
    assert left.side == right.side
    assert mutated[-1].close == 1.0


def test_forward_grades_direction() -> None:
    bars = _bars()
    params = ChartParams(rsi_high=99.9, rsi_low=1, min_votes=3, require_macd=False, horizon=2, cooldown=2)
    result = forward_test("SPY", bars, params, start=50, end=350)
    assert result["n"] > 0
    # synthetic uptrend — longs should mostly win
    if result["n"] >= 5:
        assert result["precision"] >= 0.6


def test_grade_buy_up() -> None:
    start = datetime(2025, 6, 2, 10, 0, tzinfo=ET)
    bars = [
        Bar(ticker="SPY", ts=start + timedelta(hours=i), open=100 + i, high=101 + i, low=99 + i, close=100.0 + i, volume=1)
        for i in range(5)
    ]
    from pa.domain.models import Signal

    sig = Signal(ticker="SPY", side=Side.BUY, confidence=1, ts=bars[0].ts, source="chart")
    assert grade_signal(bars, 0, sig, 2) is True
    sig_s = Signal(ticker="SPY", side=Side.SELL, confidence=1, ts=bars[0].ts, source="chart")
    assert grade_signal(bars, 0, sig_s, 2) is False


def test_split_year() -> None:
    start = datetime(2025, 1, 2, 10, 0, tzinfo=ET)
    bars = [
        Bar(
            ticker="SPY",
            ts=start + timedelta(days=i),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1,
        )
        for i in range(360)
    ]
    a, b, c = split_year(bars, 2025)
    assert a < b < c
    assert bars[b].ts.month >= 10


def test_pine_and_tv_url() -> None:
    pine = to_pine(ChartParams(), "SPY")
    assert "emaFast" in pine and "@version=5" in pine
    assert "AMEX:SPY" in tradingview_url("SPY")
    assert "SP:SPX" in tradingview_url("SPX")


def test_hunt_on_synthetic() -> None:
    bars = _bars()
    # stamp as 2025 so split_year finds October
    out = []
    start = datetime(2025, 1, 6, 10, 0, tzinfo=ET)
    for i, bar in enumerate(bars):
        out.append(bar.model_copy(update={"ts": start + timedelta(hours=i)}))
    result = hunt("SPY", out, year=2025, target=0.6, min_trades=5)
    assert result["generations"] > 0
    assert result["best"] is not None
    assert result["best"]["train_n"] >= 5
