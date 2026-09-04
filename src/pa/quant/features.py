from __future__ import annotations

from pa.domain.models import Bar


def rsi_divergence(bars: list[Bar], rsi_values: list[float | None], lookback: int = 30) -> str | None:
    """Regular bullish/bearish RSI divergence on the last `lookback` bars."""
    if len(bars) < lookback or len(rsi_values) < lookback:
        return None
    window = bars[-lookback:]
    rsi_w = rsi_values[-lookback:]
    lows = [(i, b.low) for i, b in enumerate(window)]
    highs = [(i, b.high) for i, b in enumerate(window)]
    first_low = min(lows[: lookback // 2], key=lambda x: x[1])
    last_low = min(lows[lookback // 2 :], key=lambda x: x[1])
    first_high = max(highs[: lookback // 2], key=lambda x: x[1])
    last_high = max(highs[lookback // 2 :], key=lambda x: x[1])
    r1 = rsi_w[first_low[0]]
    r2 = rsi_w[last_low[0]]
    r3 = rsi_w[first_high[0]]
    r4 = rsi_w[last_high[0]]
    if r1 is not None and r2 is not None and last_low[1] < first_low[1] and r2 > r1:
        return "bullish"
    if r3 is not None and r4 is not None and last_high[1] > first_high[1] and r4 < r3:
        return "bearish"
    return None


def support_resistance(bars: list[Bar], lookback: int = 60) -> tuple[float | None, float | None]:
    window = bars[-lookback:] if len(bars) >= lookback else bars
    if not window:
        return None, None
    return min(b.low for b in window), max(b.high for b in window)


def fibonacci_levels(bars: list[Bar], lookback: int = 80) -> dict[str, float]:
    window = bars[-lookback:] if len(bars) >= lookback else bars
    if not window:
        return {}
    lo = min(b.low for b in window)
    hi = max(b.high for b in window)
    span = hi - lo
    if span <= 0:
        return {"0": lo, "1": hi}
    return {
        "0": lo,
        "0.382": lo + span * 0.382,
        "0.5": lo + span * 0.5,
        "0.618": lo + span * 0.618,
        "1": hi,
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 10:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            continue
        out.append((closes[i] - closes[i - 1]) / closes[i - 1])
    return out


def detect_simple_pattern(bars: list[Bar]) -> str | None:
    """Heuristic patterns — not a trained model. Used as an ML-shaped feature."""
    if len(bars) < 20:
        return None
    window = bars[-20:]
    highs = [b.high for b in window]
    lows = [b.low for b in window]
    if highs[-1] > max(highs[:-1]) and lows[-1] > min(lows[:-5]):
        return "breakout"
    mid = window[len(window) // 2]
    if mid.high > max(b.high for b in window[:5]) and mid.high > max(b.high for b in window[-5:]):
        if window[-1].close < mid.close:
            return "head_and_shoulders_like"
    compression = (max(highs) - min(lows)) / (sum(b.close for b in window) / len(window))
    if compression < 0.01:
        return "coil"
    return None
