"""Intraday Call/Put setups. Fusion needs two families, except gap / momentum solo.

Families
  level     premarket_breakout, orb
  trend     ema_align, ema_cross
  momentum  momentum_burst, ema50_break
  mean_rev  bb_rejection, pullback_to_vwap, level_retest
  gap       gap_and_go

vwap_reclaim is intentionally omitted — SignalValidator measured it as a loser.
"""
from __future__ import annotations

from dataclasses import dataclass

from pa.domain.models import Bar
from pa.open_session.levels import Levels, compute_levels, split_session

FAMILY = {
    "premarket_breakout": "level",
    "orb": "level",
    "gap_and_go": "gap",
    "ema_align": "trend",
    "ema_cross": "trend",
    "vwap_side": "trend",
    "momentum_burst": "momentum",
    "ema50_break": "momentum",
    "bb_rejection": "mean_rev",
    "pullback_to_vwap": "mean_rev",
    "level_retest": "mean_rev",
}

# High-conviction names that may print without a second family.
SOLO_STRATEGIES = {"gap_and_go", "momentum_burst"}


@dataclass(frozen=True)
class Candidate:
    ticker: str
    direction: str  # call | put
    strategy: str
    family: str
    conviction: float
    trigger: float | None
    stop: float | None
    thesis: str
    last: float


def _beyond(rth: list[Bar], level: float, side: str, n: int = 2) -> bool:
    if len(rth) < n:
        return False
    if side == "above":
        return all(b.close > level for b in rth[-n:])
    return all(b.close < level for b in rth[-n:])


def _impulse(rth: list[Bar], level: float, side: str, atr: float | None) -> bool:
    """One close through the level on a full-ATR body — the dump/rip we used to miss."""
    if not rth or atr is None or atr <= 0:
        return False
    bar = rth[-1]
    body = abs(bar.close - bar.open)
    if body < 0.7 * atr:
        return False
    if side == "below":
        return bar.close < level and bar.close < bar.open
    return bar.close > level and bar.close > bar.open


def _held(rth: list[Bar], level: float, side: str, atr: float | None) -> bool:
    return _beyond(rth, level, side, n=2) or _impulse(rth, level, side, atr)


def _aligned(direction: str, lv: Levels) -> bool:
    call = direction == "call"
    ema_ok = lv.ema9 is not None and lv.ema21 is not None and (
        (call and lv.ema9 >= lv.ema21) or (not call and lv.ema9 <= lv.ema21)
    )
    vwap_ok = lv.vwap is not None and lv.last is not None and (
        (call and lv.last >= lv.vwap) or (not call and lv.last <= lv.vwap)
    )
    return bool(ema_ok or vwap_ok)


def _not_chase(price: float, level: float, atr: float | None, mult: float = 2.0) -> bool:
    if atr is None or atr <= 0:
        return True
    return abs(price - level) <= mult * atr


def _level_ok(rth: list[Bar], px: float, level: float, side: str, atr: float | None) -> bool:
    if not _held(rth, level, side, atr):
        return False
    if _impulse(rth, level, side, atr):
        return True
    return _not_chase(px, level, atr)


def _vol_burst(rth: list[Bar], mult: float = 1.5) -> bool:
    if len(rth) < 21:
        return True
    avg = sum(b.volume for b in rth[-21:-1]) / 20.0
    if avg <= 0:
        return True
    return rth[-1].volume >= mult * avg


def _cand(
    ticker: str,
    direction: str,
    strategy: str,
    conv: float,
    trigger: float | None,
    stop: float | None,
    thesis: str,
    last: float,
) -> Candidate:
    return Candidate(
        ticker, direction, strategy, FAMILY[strategy], conv, trigger, stop, thesis, last
    )


def setups_for(ticker: str, bars: list[Bar], orb_minutes: int = 15, prior_close: float | None = None) -> list[Candidate]:
    lv = compute_levels(ticker, bars, orb_minutes=orb_minutes, prior_close=prior_close)
    _, rth = split_session(bars)
    out: list[Candidate] = []
    if lv.last is None:
        return out
    px = lv.last
    ticker = ticker.upper()

    if lv.premarket_high is not None and lv.premarket_low is not None and lv.inside_premarket is False:
        if px > lv.premarket_high and _aligned("call", lv) and _level_ok(rth, px, lv.premarket_high, "above", lv.atr):
            n = "impulse" if _impulse(rth, lv.premarket_high, "above", lv.atr) and not _beyond(rth, lv.premarket_high, "above") else "2 closes"
            out.append(_cand(ticker, "call", "premarket_breakout", 1.2, lv.premarket_high, lv.premarket_low, f"{n} above PM high {lv.premarket_high:.2f}", px))
        if px < lv.premarket_low and _aligned("put", lv) and _level_ok(rth, px, lv.premarket_low, "below", lv.atr):
            n = "impulse" if _impulse(rth, lv.premarket_low, "below", lv.atr) and not _beyond(rth, lv.premarket_low, "below") else "2 closes"
            out.append(_cand(ticker, "put", "premarket_breakout", 1.2, lv.premarket_low, lv.premarket_high, f"{n} below PM low {lv.premarket_low:.2f}", px))

    if lv.orb_high is not None and lv.orb_low is not None and len(rth) >= orb_minutes + 1:
        if px > lv.orb_high and _aligned("call", lv) and _level_ok(rth, px, lv.orb_high, "above", lv.atr):
            n = "impulse" if _impulse(rth, lv.orb_high, "above", lv.atr) and not _beyond(rth, lv.orb_high, "above") else "2 closes"
            out.append(_cand(ticker, "call", "orb", 1.2, lv.orb_high, lv.orb_low, f"{n} above {orb_minutes}m ORB high {lv.orb_high:.2f}", px))
        if px < lv.orb_low and _aligned("put", lv) and _level_ok(rth, px, lv.orb_low, "below", lv.atr):
            n = "impulse" if _impulse(rth, lv.orb_low, "below", lv.atr) and not _beyond(rth, lv.orb_low, "below") else "2 closes"
            out.append(_cand(ticker, "put", "orb", 1.2, lv.orb_low, lv.orb_high, f"{n} below {orb_minutes}m ORB low {lv.orb_low:.2f}", px))

    if lv.gap_pct is not None and abs(lv.gap_pct) >= 1.0 and lv.premarket_high is not None and lv.premarket_low is not None and len(rth) <= 12:
        if lv.gap_pct >= 1.0 and px > lv.premarket_high and _aligned("call", lv):
            out.append(_cand(ticker, "call", "gap_and_go", 1.1, lv.premarket_high, lv.premarket_low, f"Gap {lv.gap_pct:.1f}% and through PM high", px))
        if lv.gap_pct <= -1.0 and px < lv.premarket_low and _aligned("put", lv):
            out.append(_cand(ticker, "put", "gap_and_go", 1.1, lv.premarket_low, lv.premarket_high, f"Gap {lv.gap_pct:.1f}% and through PM low", px))

    if lv.ema9 is not None and lv.ema21 is not None and lv.vwap is not None:
        if lv.ema9 > lv.ema21 and px > lv.vwap:
            out.append(_cand(ticker, "call", "ema_align", 1.0, lv.ema9, lv.vwap, "EMA9>EMA21 and price above VWAP", px))
        if lv.ema9 < lv.ema21 and px < lv.vwap:
            out.append(_cand(ticker, "put", "ema_align", 1.0, lv.ema9, lv.vwap, "EMA9<EMA21 and price below VWAP", px))

    out.extend(_ema_cross(ticker, rth, lv, px))
    out.extend(_momentum_burst(ticker, rth, lv, px))
    out.extend(_ema50_break(ticker, rth, lv, px))
    out.extend(_bb_rejection(ticker, rth, lv, px))
    out.extend(_pullback_to_vwap(ticker, rth, lv, px))
    out.extend(_level_retest(ticker, rth, lv, px))
    return out


def _ema_cross(ticker: str, rth: list[Bar], lv: Levels, px: float) -> list[Candidate]:
    if lv.ema9 is None or lv.ema21 is None or lv.ema9_prev is None or lv.ema21_prev is None:
        return []
    if not _vol_burst(rth, 1.0):
        return []
    if lv.ema9_prev <= lv.ema21_prev and lv.ema9 > lv.ema21:
        return [_cand(ticker, "call", "ema_cross", 0.9, lv.ema9, lv.ema21, "EMA9 crossed above EMA21", px)]
    if lv.ema9_prev >= lv.ema21_prev and lv.ema9 < lv.ema21:
        return [_cand(ticker, "put", "ema_cross", 0.9, lv.ema9, lv.ema21, "EMA9 crossed below EMA21", px)]
    return []


def _momentum_burst(ticker: str, rth: list[Bar], lv: Levels, px: float) -> list[Candidate]:
    if len(rth) < 8 or lv.atr is None or lv.atr <= 0:
        return []
    last3 = rth[-3:]
    bodies = [abs(b.close - b.open) for b in last3]
    if max(bodies) < 0.7 * lv.atr:
        return []
    if all(b.close > b.open for b in last3) and last3[-1].close > last3[0].open:
        return [_cand(ticker, "call", "momentum_burst", 1.2, last3[0].low, min(b.low for b in last3), "3 green 1m bars with ATR-sized body", px)]
    if all(b.close < b.open for b in last3) and last3[-1].close < last3[0].open:
        return [_cand(ticker, "put", "momentum_burst", 1.2, last3[0].high, max(b.high for b in last3), "3 red 1m bars with ATR-sized body", px)]
    return []


def _ema50_break(ticker: str, rth: list[Bar], lv: Levels, px: float) -> list[Candidate]:
    if lv.ema50 is None or len(rth) < 2:
        return []
    prev, last = rth[-2], rth[-1]
    e = lv.ema50
    if prev.close >= e > last.close and last.close < last.open:
        return [_cand(ticker, "put", "ema50_break", 1.1, e, e, f"Broke below EMA50 {e:.2f}", px)]
    if prev.close <= e < last.close and last.close > last.open:
        return [_cand(ticker, "call", "ema50_break", 1.1, e, e, f"Broke above EMA50 {e:.2f}", px)]
    return []


def _bb_rejection(ticker: str, rth: list[Bar], lv: Levels, px: float) -> list[Candidate]:
    """Failed rip to the upper band → put; failed dump to the lower band → call."""
    if lv.bb_mid is None or lv.bb_upper is None or lv.bb_lower is None or len(rth) < 8:
        return []
    look = rth[-12:]
    tagged_up = any(b.high >= lv.bb_upper for b in look)
    tagged_dn = any(b.low <= lv.bb_lower for b in look)
    last = rth[-1]
    out: list[Candidate] = []
    if tagged_up and last.close < lv.bb_mid and last.close < last.open:
        if lv.ema50 is None or last.close < lv.ema50:
            stop = max(b.high for b in look)
            out.append(_cand(ticker, "put", "bb_rejection", 1.2, lv.bb_mid, stop, f"Rejected upper BB {lv.bb_upper:.2f}, close under mid {lv.bb_mid:.2f}", px))
    if tagged_dn and last.close > lv.bb_mid and last.close > last.open:
        if lv.ema50 is None or last.close > lv.ema50:
            stop = min(b.low for b in look)
            out.append(_cand(ticker, "call", "bb_rejection", 1.2, lv.bb_mid, stop, f"Rejected lower BB {lv.bb_lower:.2f}, close over mid {lv.bb_mid:.2f}", px))
    return out


def _pullback_to_vwap(ticker: str, rth: list[Bar], lv: Levels, px: float) -> list[Candidate]:
    if lv.vwap is None or lv.atr is None or lv.atr <= 0 or lv.orb_high is None or lv.orb_low is None or len(rth) < 8:
        return []
    if abs(px - lv.vwap) > 0.6 * lv.atr:
        return []
    last, prev = rth[-1], rth[-2]
    bullish_bias = (
        (lv.premarket_high is not None and any(b.high > lv.premarket_high for b in rth))
        or any(b.high > lv.orb_high for b in rth)
    )
    if bullish_bias and _aligned("call", lv):
        if last.close > last.open and last.close >= lv.vwap and prev.close <= last.close:
            return [_cand(ticker, "call", "pullback_to_vwap", 1.2, lv.vwap, lv.vwap - 0.5 * lv.atr, f"Bullish pullback to VWAP {lv.vwap:.2f}", px)]
    bearish_bias = (
        (lv.premarket_low is not None and any(b.low < lv.premarket_low for b in rth))
        or any(b.low < lv.orb_low for b in rth)
    )
    if bearish_bias and _aligned("put", lv):
        if last.close < last.open and last.close <= lv.vwap and prev.close >= last.close:
            return [_cand(ticker, "put", "pullback_to_vwap", 1.2, lv.vwap, lv.vwap + 0.5 * lv.atr, f"Bearish VWAP rejection {lv.vwap:.2f}", px)]
    return []


def _level_retest(ticker: str, rth: list[Bar], lv: Levels, px: float) -> list[Candidate]:
    if lv.atr is None or lv.atr <= 0 or len(rth) < 10:
        return []
    atr = lv.atr
    zone = 0.4 * atr
    last = rth[-1]
    ups = [("ORB high", lv.orb_high), ("PM high", lv.premarket_high)]
    downs = [("ORB low", lv.orb_low), ("PM low", lv.premarket_low)]
    for name, level in ups:
        if level is None:
            continue
        if not any(b.close > level + atr for b in rth[:-1]):
            continue
        if px <= level or abs(px - level) > zone:
            continue
        if last.low > level + zone or last.close <= level:
            continue
        if not _aligned("call", lv):
            continue
        return [_cand(ticker, "call", "level_retest", 1.2, level, level - 0.5 * atr, f"Retested broken {name} {level:.2f} and held", px)]
    for name, level in downs:
        if level is None:
            continue
        if not any(b.close < level - atr for b in rth[:-1]):
            continue
        if px >= level or abs(px - level) > zone:
            continue
        if last.high < level - zone or last.close >= level:
            continue
        if not _aligned("put", lv):
            continue
        return [_cand(ticker, "put", "level_retest", 1.2, level, level + 0.5 * atr, f"Retested broken {name} {level:.2f} and rejected", px)]
    return []
