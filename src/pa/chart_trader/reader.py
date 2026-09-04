from __future__ import annotations

from dataclasses import dataclass

from pa.chart_trader.params import ChartParams
from pa.domain.models import Bar, Side, Signal
from pa.quant.indicators import ema, macd, resample_n, rsi, session_vwap


@dataclass
class Tape:
    closes: list[float]
    ema_fast: list[float | None]
    ema_slow: list[float | None]
    vwap: list[float | None]
    rsi: list[float | None]
    macd_hist: list[float | None]
    ht_ema_fast: list[float | None]
    ht_ema_slow: list[float | None]
    dt_ema_fast: list[float | None]
    dt_ema_slow: list[float | None]
    ht_n: int
    dt_n: int


def precompute(window: list[Bar], params: ChartParams) -> Tape:
    closes = [b.close for b in window]
    _, _, hist = macd(closes)
    ht = resample_n(window, params.ht_fast)
    dt = resample_n(window, params.ht_slow)
    ht_f = ema([b.close for b in ht], params.ema_fast) if ht else []
    ht_s = ema([b.close for b in ht], params.ema_slow) if ht else []
    dt_f = ema([b.close for b in dt], params.ema_fast) if dt else []
    dt_s = ema([b.close for b in dt], params.ema_slow) if dt else []
    return Tape(
        closes=closes,
        ema_fast=ema(closes, params.ema_fast),
        ema_slow=ema(closes, params.ema_slow),
        vwap=session_vwap(window),
        rsi=rsi(closes, params.rsi_period),
        macd_hist=hist,
        ht_ema_fast=ht_f,
        ht_ema_slow=ht_s,
        dt_ema_fast=dt_f,
        dt_ema_slow=dt_s,
        ht_n=params.ht_fast,
        dt_n=params.ht_slow,
    )


def _ht_index(i: int, n: int) -> int | None:
    """Last higher-TF bucket fully contained in bars[0..i] (no look-ahead)."""
    complete = (i + 1) // n
    if complete <= 0:
        return None
    return complete - 1


def signal_at(ticker: str, bars: list[Bar], i: int, tape: Tape, params: ChartParams) -> Signal:
    ts = bars[i].ts
    last = tape.closes[i]
    fast = tape.ema_fast[i]
    slow = tape.ema_slow[i]
    vwap = tape.vwap[i]
    rsi_1 = tape.rsi[i]
    macd_h = tape.macd_hist[i]
    hi = _ht_index(i, tape.ht_n)
    di = _ht_index(i, tape.dt_n)
    ht_fast = tape.ht_ema_fast[hi] if hi is not None and hi < len(tape.ht_ema_fast) else None
    ht_slow = tape.ht_ema_slow[hi] if hi is not None and hi < len(tape.ht_ema_slow) else None
    dt_fast = tape.dt_ema_fast[di] if di is not None and di < len(tape.dt_ema_fast) else None
    dt_slow = tape.dt_ema_slow[di] if di is not None and di < len(tape.dt_ema_slow) else None

    reasons: list[str] = []
    bull = 0
    bear = 0
    votes = 0

    def vote(flag: bool | None, label: str) -> None:
        nonlocal bull, bear, votes
        if flag is None:
            reasons.append(f"{label}:na")
            return
        votes += 1
        if flag:
            bull += 1
            reasons.append(f"{label}:bull")
        else:
            bear += 1
            reasons.append(f"{label}:bear")

    vote(None if fast is None or slow is None else fast > slow, "ema")
    if params.require_vwap:
        vote(None if vwap is None else last > vwap, "vwap")
    vote(None if ht_fast is None or ht_slow is None else ht_fast > ht_slow, "ht")
    vote(None if dt_fast is None or dt_slow is None else dt_fast > dt_slow, "dt")
    if params.require_macd:
        vote(None if macd_h is None else macd_h > 0, "macd")

    rsi_ok = rsi_1 is not None and params.rsi_low <= rsi_1 <= params.rsi_high
    if rsi_1 is None:
        reasons.append("rsi:na")
    elif not rsi_ok:
        reasons.append(f"rsi_extreme:{rsi_1:.1f}")
    else:
        reasons.append(f"rsi_ok:{rsi_1:.1f}")

    sep_ok = True
    if params.min_sep > 0 and fast is not None and slow is not None and last:
        sep_ok = abs(fast - slow) / last >= params.min_sep
        reasons.append(f"sep:{abs(fast - slow) / last:.5f}")

    side = Side.FLAT
    confidence = max(bull, bear) / votes if votes else 0.0
    if rsi_ok and sep_ok and votes >= params.min_votes:
        if bull >= params.min_votes and bull > bear:
            side = Side.BUY
            confidence = bull / votes
        elif bear >= params.min_votes and bear > bull:
            side = Side.SELL
            confidence = bear / votes
    return Signal(
        ticker=ticker,
        side=side,
        confidence=round(confidence, 4),
        reasons=reasons,
        source="chart",
        ts=ts,
    )


def read_chart(ticker: str, window: list[Bar], params: ChartParams) -> Signal:
    """Guess direction using only bars in `window` (no future)."""
    need = max(params.ema_slow, params.rsi_period, 26) + 5
    if len(window) < need:
        return Signal(
            ticker=ticker,
            side=Side.FLAT,
            confidence=0.0,
            reasons=["insufficient"],
            ts=window[-1].ts,
            source="chart",
        )
    tape = precompute(window, params)
    return signal_at(ticker, window, len(window) - 1, tape, params)
