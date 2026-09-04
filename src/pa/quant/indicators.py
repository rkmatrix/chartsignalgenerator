from __future__ import annotations

from datetime import datetime

from pa.domain.models import Bar, Timeframe


def ema(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    k = 2.0 / (period + 1)
    out: list[float | None] = []
    prev: float | None = None
    for i, price in enumerate(values):
        if i < period - 1:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[:period]) / period
            out.append(prev)
            continue
        prev = price * k + prev * (1.0 - k)
        out.append(prev)
    return out


def session_vwap(bars: list[Bar]) -> list[float | None]:
    """Reset VWAP at each ET calendar day."""
    out: list[float | None] = []
    cum_pv = 0.0
    cum_v = 0.0
    current_day = None
    for bar in bars:
        day = bar.ts.date()
        if current_day != day:
            current_day = day
            cum_pv = 0.0
            cum_v = 0.0
        typical = (bar.high + bar.low + bar.close) / 3.0
        cum_pv += typical * bar.volume
        cum_v += bar.volume
        out.append(cum_pv / cum_v if cum_v else None)
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None]
    gains: list[float] = []
    losses: list[float] = []
    avg_gain: float | None = None
    avg_loss: float | None = None
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if i < period:
            gains.append(gain)
            losses.append(loss)
            out.append(None)
            continue
        if avg_gain is None:
            gains.append(gain)
            losses.append(loss)
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: list[float | None] = []
    for a, b in zip(fast_ema, slow_ema, strict=True):
        line.append(None if a is None or b is None else a - b)
    compact = [x for x in line if x is not None]
    pad = len(line) - len(compact)
    signal_compact = ema(compact, signal_period) if compact else []
    signal: list[float | None] = [None] * pad + signal_compact
    hist: list[float | None] = []
    for macd_v, sig in zip(line, signal, strict=True):
        hist.append(None if macd_v is None or sig is None else macd_v - sig)
    return line, signal, hist


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    if minutes <= 1 or not bars:
        return list(bars)
    grouped: dict[datetime, list[Bar]] = {}
    for bar in bars:
        epoch = int(bar.ts.timestamp())
        bucket = epoch - (epoch % (minutes * 60))
        key = datetime.fromtimestamp(bucket, tz=bar.ts.tzinfo)
        grouped.setdefault(key, []).append(bar)
    out: list[Bar] = []
    tf = Timeframe.M5 if minutes == 5 else Timeframe.M15 if minutes == 15 else Timeframe.M1
    for ts in sorted(grouped):
        chunk = grouped[ts]
        out.append(
            Bar(
                ticker=chunk[0].ticker,
                ts=ts,
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
                timeframe=tf,
            )
        )
    return out


def last_valid(series: list[float | None]) -> float | None:
    for value in reversed(series):
        if value is not None:
            return value
    return None


def bollinger(
    values: list[float], period: int = 20, n_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """SMA mid ± n_std sample stdev (TradingView-style Bollinger)."""
    mid: list[float | None] = []
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            mid.append(None)
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / max(period - 1, 1)
        sd = var ** 0.5
        mid.append(m)
        upper.append(m + n_std * sd)
        lower.append(m - n_std * sd)
    return mid, upper, lower


def resample_n(bars: list[Bar], n: int) -> list[Bar]:
    """Group every n bars (used when the tape is already 1h, not 1m)."""
    if n <= 1 or not bars:
        return list(bars)
    out: list[Bar] = []
    for i in range(0, len(bars), n):
        chunk = bars[i : i + n]
        if not chunk:
            continue
        out.append(
            Bar(
                ticker=chunk[0].ticker,
                ts=chunk[0].ts,
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
                timeframe=chunk[0].timeframe,
            )
        )
    return out
