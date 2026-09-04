from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.data.fixtures import generate_trend_bars
from pa.quant.indicators import bollinger, ema, macd, resample, rsi, session_vwap

ET = ZoneInfo("America/New_York")


def test_ema_constant_series() -> None:
    values = [10.0] * 20
    series = ema(values, 5)
    assert series[3] is None
    assert series[4] == 10.0
    assert series[-1] == 10.0


def test_ema_rises_with_prices() -> None:
    values = [float(i) for i in range(1, 30)]
    series = ema(values, 5)
    assert series[-1] is not None and series[-2] is not None
    assert series[-1] > series[-2]


def test_vwap_between_low_and_high() -> None:
    start = datetime(2026, 3, 10, 9, 30, tzinfo=ET)
    bars = generate_trend_bars("SPY", start, count=30, start_price=100.0, drift=0.05)
    vwap = session_vwap(bars)
    last = vwap[-1]
    assert last is not None
    assert min(b.low for b in bars) <= last <= max(b.high for b in bars)


def test_rsi_uptrend_is_high() -> None:
    values = [100.0 + i for i in range(30)]
    series = rsi(values, 14)
    assert series[-1] is not None
    assert series[-1] > 70


def test_rsi_downtrend_is_low() -> None:
    values = [100.0 - i for i in range(30)]
    series = rsi(values, 14)
    assert series[-1] is not None
    assert series[-1] < 30


def test_bollinger_contains_price() -> None:
    values = [100.0 + 0.1 * i for i in range(30)]
    mid, upper, lower = bollinger(values, 20, 2.0)
    assert mid[-1] is not None and upper[-1] is not None and lower[-1] is not None
    assert lower[-1] < mid[-1] < upper[-1]
    assert lower[-1] < values[-1] < upper[-1]


def test_macd_returns_three_series() -> None:
    values = [100.0 + 0.2 * i for i in range(50)]
    line, signal, hist = macd(values)
    assert len(line) == len(signal) == len(hist) == 50
    assert hist[-1] is not None


def test_resample_1m_to_5m() -> None:
    start = datetime(2026, 3, 10, 9, 30, tzinfo=ET)
    bars = generate_trend_bars("SPY", start, count=20, start_price=100.0, drift=0.1)
    out = resample(bars, 5)
    assert len(out) == 4
    assert out[0].open == bars[0].open
    assert out[0].close == bars[4].close
    assert out[0].high == max(b.high for b in bars[:5])
