from __future__ import annotations

from pa.chart_trader.params import ChartParams
from pa.chart_trader.reader import precompute, signal_at
from pa.domain.models import Bar, Side, Signal


def grade_signal(bars: list[Bar], index: int, signal: Signal, horizon: int) -> bool | None:
    """True if price moved in the predicted direction `horizon` bars later. Look-ahead only for grading."""
    future_i = index + horizon
    if future_i >= len(bars) or signal.side == Side.FLAT:
        return None
    now = bars[index].close
    later = bars[future_i].close
    if now == later:
        return None
    if signal.side == Side.BUY:
        return later > now
    return later < now


def forward_test(
    ticker: str,
    bars: list[Bar],
    params: ChartParams,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    """Walk the tape: at bar i, indicators are causal; grade uses i+horizon only."""
    start = start or max(params.ema_slow + 30, 40)
    end = end if end is not None else (len(bars) - params.horizon)
    end = min(end, len(bars) - params.horizon)
    start = max(0, min(start, end))
    if end - start < 10:
        return {
            "wins": 0,
            "losses": 0,
            "n": 0,
            "precision": 0.0,
            "samples": [],
            "params": params.as_dict(),
        }
    tape = precompute(bars, params)
    wins = 0
    losses = 0
    samples: list[dict] = []
    last_signal_at = -10_000
    for i in range(start, end):
        if i - last_signal_at < params.cooldown:
            continue
        signal = signal_at(ticker, bars, i, tape, params)
        if signal.side == Side.FLAT:
            continue
        hit = grade_signal(bars, i, signal, params.horizon)
        if hit is None:
            continue
        last_signal_at = i
        if hit:
            wins += 1
        else:
            losses += 1
        if len(samples) < 40:
            samples.append(
                {
                    "ts": bars[i].ts.isoformat(),
                    "side": signal.side.value,
                    "entry": bars[i].close,
                    "exit": bars[i + params.horizon].close,
                    "hit": hit,
                    "confidence": signal.confidence,
                    "reasons": signal.reasons[:8],
                }
            )
    n = wins + losses
    precision = wins / n if n else 0.0
    return {
        "wins": wins,
        "losses": losses,
        "n": n,
        "precision": precision,
        "samples": samples,
        "params": params.as_dict(),
    }
