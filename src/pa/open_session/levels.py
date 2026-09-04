from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from pa.clock import RTH_OPEN
from pa.domain.models import Bar
from pa.quant.indicators import bollinger, ema, last_valid, session_vwap

ET = ZoneInfo("America/New_York")


@dataclass
class Levels:
    ticker: str
    last: float | None = None
    vwap: float | None = None
    ema9: float | None = None
    ema21: float | None = None
    ema50: float | None = None
    ema9_prev: float | None = None
    ema21_prev: float | None = None
    atr: float | None = None
    bb_mid: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    premarket_high: float | None = None
    premarket_low: float | None = None
    orb_high: float | None = None
    orb_low: float | None = None
    prior_close: float | None = None
    gap_pct: float | None = None
    inside_premarket: bool | None = None
    rth_n: int = 0
    pm_n: int = 0


def split_session(bars: list[Bar]) -> tuple[list[Bar], list[Bar]]:
    pm: list[Bar] = []
    rth: list[Bar] = []
    for bar in bars:
        clock = bar.ts.astimezone(ET).time().replace(tzinfo=None)
        if clock < RTH_OPEN:
            pm.append(bar)
        else:
            rth.append(bar)
    return pm, rth


def _atr(bars: list[Bar], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        high, low, prev = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def compute_levels(ticker: str, bars: list[Bar], orb_minutes: int = 15, prior_close: float | None = None) -> Levels:
    pm, rth = split_session(bars)
    lv = Levels(ticker=ticker.upper(), rth_n=len(rth), pm_n=len(pm), prior_close=prior_close)
    if pm:
        lv.premarket_high = max(b.high for b in pm)
        lv.premarket_low = min(b.low for b in pm)
    if rth:
        lv.last = rth[-1].close
        opening = rth[: max(1, orb_minutes)]
        lv.orb_high = max(b.high for b in opening)
        lv.orb_low = min(b.low for b in opening)
        lv.vwap = last_valid(session_vwap(rth))
        closes = [b.close for b in rth]
        e9 = ema(closes, 9)
        e21 = ema(closes, 21)
        e50 = ema(closes, 50)
        lv.ema9 = last_valid(e9)
        lv.ema21 = last_valid(e21)
        lv.ema50 = last_valid(e50)
        if len(closes) >= 10:
            lv.ema9_prev = e9[-2] if e9[-2] is not None else None
        if len(closes) >= 22:
            lv.ema21_prev = e21[-2] if e21[-2] is not None else None
        mid, up, lo = bollinger(closes, 20, 2.0)
        lv.bb_mid = last_valid(mid)
        lv.bb_upper = last_valid(up)
        lv.bb_lower = last_valid(lo)
        lv.atr = _atr(rth)
        ref = rth[0].open
        if prior_close and prior_close > 0:
            lv.gap_pct = round((ref - prior_close) / prior_close * 100.0, 2)
    elif pm:
        lv.last = pm[-1].close
    if lv.last is not None and lv.premarket_high is not None and lv.premarket_low is not None:
        lv.inside_premarket = lv.premarket_low <= lv.last <= lv.premarket_high
    return lv
