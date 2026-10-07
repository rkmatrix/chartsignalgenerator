"""AlphaWave options signals, the same rules as the TradingView indicator.

Source of truth is MultiConfluence_Signal_Indicator.pine in
ChartSignalGenerator: a closed 5-minute bar, two ways in (value-zone pullback
or a volume breakout), and a take-profit when price loses the 9 EMA or the
MACD histogram crosses back through zero.

The desk used to invent its own setups. Those are what the book has been
losing on. This module is what the chart is already drawing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pa.domain.models import Bar, Timeframe
from pa.open_session.fuse import FusedSignal
from pa.open_session.playbook import session_window

ET = ZoneInfo("America/New_York")

BAR_MINUTES = 5
MIN_BARS = 50
EMA_FAST = 9
EMA_MED = 21
EMA_SLOW = 50
RSI_LEN = 14
RSI_BULL = 45.0
RSI_BEAR = 55.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ATR_LEN = 14
VOL_LEN = 20
VOL_PULLBACK = 0.85
VOL_BREAKOUT = 1.10
SL_ATR = 1.2
TP1_ATR = 1.5
TP2_ATR = 2.5


@dataclass(frozen=True)
class WaveEvent:
    kind: str  # CALL, PUT, TP_CALL, TP_PUT
    price: float
    bar_ts: datetime
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    engine: str = ""


@dataclass(frozen=True)
class WaveResult:
    position: str  # CALL, PUT, FLAT
    event: WaveEvent | None
    last_bar: datetime | None


RTH_START = time(9, 30)
RTH_END = time(16, 0)


def _regular_session(ts: datetime) -> bool:
    clock = ts.time().replace(tzinfo=None)
    return RTH_START <= clock < RTH_END


def resample_5m(bars: list[Bar], now: datetime | None = None) -> list[Bar]:
    """Fold 1-minute regular-session bars into closed 5-minute bars.

    Extended hours are dropped, matching a TradingView chart on its default
    regular session. Our premarket feed prints volume 0, which made the
    "volume >= 85% of average" check compare 0 with 0 and pass: on 2026-09-30
    the 09:25 premarket bar fired six entries at 09:30:06, before a single
    regular-session bar had closed, and the first real bar then read as a
    volume surge against an average of zeros.

    The bar still forming is left out, matching the indicator's
    once-per-bar-close alerts.
    """
    if not bars:
        return []
    buckets: dict[datetime, list[Bar]] = {}
    for bar in bars:
        ts = bar.ts.astimezone(ET) if bar.ts.tzinfo else bar.ts.replace(tzinfo=ET)
        if not _regular_session(ts):
            continue
        start = ts.replace(minute=(ts.minute // BAR_MINUTES) * BAR_MINUTES, second=0, microsecond=0)
        buckets.setdefault(start, []).append(bar)
    out: list[Bar] = []
    for start in sorted(buckets):
        end = start + timedelta(minutes=BAR_MINUTES)
        if now is not None:
            clock = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            if end > clock:
                continue
        group = sorted(buckets[start], key=lambda b: b.ts)
        out.append(
            Bar(
                ticker=group[0].ticker,
                ts=start,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum(b.volume for b in group),
                timeframe=Timeframe.M5,
            )
        )
    return out


def _ema(values: list[float | None], length: int) -> list[float | None]:
    """Pine ta.ema: SMA seed, then the standard recursive EMA. NaN until warm."""
    out: list[float | None] = [None] * len(values)
    buf: list[float] = []
    prev: float | None = None
    k = 2.0 / (length + 1)
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev is None:
            buf.append(value)
            if len(buf) == length:
                prev = sum(buf) / length
                out[i] = prev
            continue
        prev = value * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _rsi(closes: list[float], length: int = RSI_LEN) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= length:
        return out
    gain_ch = []
    loss_ch = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain_ch.append(max(change, 0.0))
        loss_ch.append(max(-change, 0.0))
    avg_g = sum(gain_ch[:length]) / length
    avg_l = sum(loss_ch[:length]) / length
    out[length] = 100.0 if avg_l == 0 else 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    for j in range(length, len(gain_ch)):
        avg_g = (avg_g * (length - 1) + gain_ch[j]) / length
        avg_l = (avg_l * (length - 1) + loss_ch[j]) / length
        if avg_l == 0:
            out[j + 1] = 100.0
        else:
            out[j + 1] = 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    return out


def _atr(bars: list[Bar], length: int = ATR_LEN) -> list[float | None]:
    trs: list[float | None] = [None]
    for i in range(1, len(bars)):
        high, low, prev = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    return _ema_rma(trs, length)


def _ema_rma(values: list[float | None], length: int) -> list[float | None]:
    """Wilder RMA with an SMA seed, skipping leading Nones."""
    out: list[float | None] = [None] * len(values)
    buf: list[float] = []
    prev: float | None = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev is None:
            buf.append(value)
            if len(buf) == length:
                prev = sum(buf) / length
                out[i] = prev
            continue
        prev = (prev * (length - 1) + value) / length
        out[i] = prev
    return out


def _sma(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    total = sum(values[:length])
    out[length - 1] = total / length
    for i in range(length, len(values)):
        total += values[i] - values[i - length]
        out[i] = total / length
    return out


def evaluate(bars_1m: list[Bar], now: datetime | None = None) -> WaveResult:
    """Run the indicator across closed 5-minute bars.

    `event` is set only when the latest closed bar is itself a CALL, PUT, or
    take-profit. Older signals stay in the position state and are not re-fired.
    """
    bars = resample_5m(bars_1m, now)
    if len(bars) < MIN_BARS:
        return WaveResult("FLAT", None, bars[-1].ts if bars else None)
    events, position = _run(bars)
    fresh = events[-1] if events and events[-1].bar_ts == bars[-1].ts else None
    return WaveResult(position, fresh, bars[-1].ts)


def all_events(bars_1m: list[Bar]) -> list[WaveEvent]:
    """Every CALL, PUT and take-profit the indicator prints, in one pass."""
    bars = resample_5m(bars_1m)
    if len(bars) < MIN_BARS:
        return []
    return _run(bars)[0]


def _run(bars: list[Bar]) -> tuple[list[WaveEvent], str]:

    closes = [b.close for b in bars]
    ema9 = _ema(closes, EMA_FAST)
    ema21 = _ema(closes, EMA_MED)
    ema50 = _ema(closes, EMA_SLOW)
    rsi = _rsi(closes)
    ema12 = _ema(closes, MACD_FAST)
    ema26 = _ema(closes, MACD_SLOW)
    macd = [None if a is None or b is None else a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd, MACD_SIGNAL)
    hist = [None if m is None or s is None else m - s for m, s in zip(macd, signal)]
    atr = _atr(bars)
    vol_ma = _sma([b.volume for b in bars], VOL_LEN)

    direction = 0  # 1 call, -1 put, 0 flat
    in_call = False
    in_put = False
    events: list[WaveEvent] = []

    for i in range(1, len(bars)):
        needed = (
            ema9[i], ema9[i - 1], ema21[i], ema50[i],
            rsi[i], rsi[i - 1], hist[i], hist[i - 1], atr[i], vol_ma[i],
        )
        if any(v is None for v in needed):
            continue
        bar = bars[i]
        prev = bars[i - 1]
        e9, e9_prev, e21, e50 = ema9[i], ema9[i - 1], ema21[i], ema50[i]
        rsi_now, rsi_prev = rsi[i], rsi[i - 1]
        hist_now, hist_prev = hist[i], hist[i - 1]
        vol_ok_pb = bar.volume >= vol_ma[i] * VOL_PULLBACK
        vol_ok_bo = bar.volume >= vol_ma[i] * VOL_BREAKOUT

        bull_trend = bar.close > e50 and e21 > e50
        bear_trend = bar.close < e50 and e21 < e50
        bull_dip = bar.low <= e21 * 1.012 and bar.close >= e50 * 0.975
        bear_rally = bar.high >= e21 * 0.988 and bar.close <= e50 * 1.025
        bull_trigger = (rsi_now > RSI_BULL and rsi_prev <= RSI_BULL) or (hist_now > 0 and hist_prev <= 0)
        bear_trigger = (rsi_now < RSI_BEAR and rsi_prev >= RSI_BEAR) or (hist_now < 0 and hist_prev >= 0)
        pullback_call = bull_trend and bull_dip and bull_trigger and vol_ok_pb
        pullback_put = bear_trend and bear_rally and bear_trigger and vol_ok_pb

        breakout_call = (
            bar.close > e9 and bar.close > e21 and bar.close > bar.open
            and vol_ok_bo and hist_now > 0 and rsi_now >= 46 and hist_now > hist_prev
        )
        breakout_put = (
            bar.close < e9 and bar.close < e21 and bar.close < bar.open
            and vol_ok_bo and hist_now < 0 and rsi_now <= 54 and hist_now < hist_prev
        )
        raw_call = pullback_call or breakout_call
        raw_put = pullback_put or breakout_put
        call_sig = raw_call and direction != 1
        put_sig = raw_put and direction != -1 and not call_sig

        engine = ""
        if call_sig:
            engine = "pullback" if pullback_call else "breakout"
        elif put_sig:
            engine = "pullback" if pullback_put else "breakout"

        # Take-profit is evaluated against the position held coming into the
        # bar. An entry on this bar is not exited on this bar.
        tp_call = (
            in_call and not call_sig
            and ((bar.close < e9 and prev.close >= e9_prev) or (hist_now < 0 and hist_prev >= 0))
        )
        tp_put = (
            in_put and not put_sig
            and ((bar.close > e9 and prev.close <= e9_prev) or (hist_now > 0 and hist_prev <= 0))
        )

        event: WaveEvent | None = None
        if call_sig:
            direction = 1
            in_call = True
            in_put = False
            event = WaveEvent(
                "CALL", bar.close, bar.ts,
                stop=round(bar.close - atr[i] * SL_ATR, 2),
                tp1=round(bar.close + atr[i] * TP1_ATR, 2),
                tp2=round(bar.close + atr[i] * TP2_ATR, 2),
                engine=engine,
            )
        elif put_sig:
            direction = -1
            in_put = True
            in_call = False
            event = WaveEvent(
                "PUT", bar.close, bar.ts,
                stop=round(bar.close + atr[i] * SL_ATR, 2),
                tp1=round(bar.close - atr[i] * TP1_ATR, 2),
                tp2=round(bar.close - atr[i] * TP2_ATR, 2),
                engine=engine,
            )
        elif tp_call:
            in_call = False
            direction = 0
            event = WaveEvent("TP_CALL", bar.close, bar.ts)
        elif tp_put:
            in_put = False
            direction = 0
            event = WaveEvent("TP_PUT", bar.close, bar.ts)

        if event is not None:
            events.append(event)

    position = "CALL" if in_call else ("PUT" if in_put else "FLAT")
    return events, position


def to_fused(ticker: str, event: WaveEvent, elapsed: float | None) -> FusedSignal | None:
    """A CALL/PUT event as a desk signal. Take-profits are exits, not entries."""
    if event.kind not in {"CALL", "PUT"}:
        return None
    direction = "call" if event.kind == "CALL" else "put"
    engine = event.engine or "confluence"
    label = "value-zone pullback" if engine == "pullback" else "volume breakout"
    tp1 = f"{event.tp1:.2f}" if event.tp1 is not None else "—"
    tp2 = f"{event.tp2:.2f}" if event.tp2 is not None else "—"
    stop = f"{event.stop:.2f}" if event.stop is not None else "—"
    return FusedSignal(
        ticker=ticker.upper(),
        direction=direction,
        strategies=[f"alphawave_{engine}"],
        families=["alphawave"],
        conviction=2.0 if engine == "pullback" else 1.6,
        trigger=event.price,
        stop=event.stop,
        thesis=(
            f"AlphaWave {event.kind} — {label} on the closed 5m bar. "
            f"Stop {stop}, TP1 {tp1}, TP2 {tp2}. "
            f"Exit when the indicator prints take profit."
        ),
        last=event.price,
        playbook="AlphaWave 5m confluence",
        window=session_window(elapsed) or "",
        signal_bar=event.bar_ts.isoformat(),
    )
