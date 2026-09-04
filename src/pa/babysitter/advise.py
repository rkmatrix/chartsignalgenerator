"""HOLD / SCALE_OUT / TAKE_PROFIT / STRONG_SELL / HARD_SELL — SignalValidator lesson.

Cut losers faster than we let winners run:
  * plan stop on 0–2 DTE premium (−25%, same as SignalValidator Own Signals)
  * underlying stop from the setup (ORB low/high, PM range)
  * failed breakout = back through the trigger
  * tape flip = fused signal is the other way
  * structure break (VWAP + 9EMA against) while red

A red mark is not a sell if the thesis is still intact and no stop has printed
(TSLA 310C). Advisory only. PA never auto-sells Robinhood.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HARD_STOP_PCT = -50.0
TAKE_PROFIT_PCT = 75.0
SCALE_OUT_PCT = 50.0
EOD_FLATTEN_MINUTES = 20
SPREAD_NOISE_PCT = -8.0
PLAN_STOP_0DTE_PCT = 25.0
PLAN_STOP_SHORT_PCT = 40.0
SWING_MIN_DTE = 5


@dataclass
class Advice:
    action: str
    confidence: float
    headline: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "confidence": round(self.confidence, 2),
            "headline": self.headline,
            "reasons": list(self.reasons),
        }


def default_plan_stop_pct(days_to_expiry: int | None, is_0dte: bool = False) -> float | None:
    if is_0dte or (days_to_expiry is not None and days_to_expiry <= 2):
        return PLAN_STOP_0DTE_PCT
    if days_to_expiry is not None and days_to_expiry < SWING_MIN_DTE:
        return PLAN_STOP_SHORT_PCT
    return None


def structure_intact(is_call: bool, underlying: float | None, vwap: float | None, ema_rising: bool | None) -> bool | None:
    if underlying is None or vwap is None:
        return None
    on_side = underlying >= vwap if is_call else underlying <= vwap
    if ema_rising is None:
        return on_side
    return on_side or (ema_rising if is_call else not ema_rising)


def advise(
    *,
    is_call: bool,
    entry: float,
    mark: float | None,
    underlying: float | None = None,
    vwap: float | None = None,
    ema9: float | None = None,
    ema9_prev: float | None = None,
    minutes_to_close: int | None = None,
    is_0dte: bool = False,
    peak_mark: float | None = None,
    days_to_expiry: int | None = None,
    plan_stop_pct: float | None = None,
    underlying_stop: float | None = None,
    trigger: float | None = None,
    tape_direction: str | None = None,
    minutes_held: float | None = None,
) -> Advice:
    if mark is None or entry <= 0:
        return Advice("HOLD", 0.2, "Holding — no live option quote", ["no mark"])
    pnl = (mark - entry) / entry * 100.0
    ema_rising = None if ema9 is None or ema9_prev is None else ema9 > ema9_prev
    intact = structure_intact(is_call, underlying, vwap, ema_rising)
    reasons = [f"P&L {pnl:+.1f}%"]
    if intact is True:
        reasons.append("structure intact (VWAP/EMA)")
    elif intact is False:
        reasons.append("structure broken vs VWAP/EMA")
    else:
        reasons.append("no underlying tape")
    swing = days_to_expiry is not None and days_to_expiry >= SWING_MIN_DTE
    if plan_stop_pct is None:
        plan_stop_pct = default_plan_stop_pct(days_to_expiry, is_0dte)
    chart_ok = minutes_held is None or minutes_held >= 5

    side = "call" if is_call else "put"
    if tape_direction in {"call", "put"} and tape_direction != side:
        return Advice(
            "STRONG_SELL",
            0.85,
            f"Prediction failed — tape flipped to {tape_direction.upper()}",
            reasons + [f"live signal is {tape_direction}"],
        )

    if (
        plan_stop_pct
        and plan_stop_pct > 0
        and pnl <= min(-float(plan_stop_pct), SPREAD_NOISE_PCT)
    ):
        return Advice(
            "HARD_SELL",
            0.9,
            f"Plan stop — down {abs(pnl):.0f}% (max loss −{plan_stop_pct:.0f}%)",
            reasons + [f"plan stop −{plan_stop_pct:.0f}%"],
        )

    if is_0dte and minutes_to_close is not None and minutes_to_close <= EOD_FLATTEN_MINUTES:
        action = "TAKE_PROFIT" if pnl > SPREAD_NOISE_PCT else "HARD_SELL"
        return Advice(action, 0.9, f"0DTE flatten — {minutes_to_close}m to the bell", reasons)

    if chart_ok and underlying is not None and underlying_stop is not None:
        hit = (is_call and underlying < underlying_stop) or ((not is_call) and underlying > underlying_stop)
        if hit:
            return Advice(
                "HARD_SELL",
                0.9,
                f"Prediction failed — underlying through SL {underlying_stop:.2f}",
                reasons + [f"spot {underlying:.2f} vs stop {underlying_stop:.2f}"],
            )

    if chart_ok and underlying is not None and trigger is not None:
        lost = (is_call and underlying < trigger) or ((not is_call) and underlying > trigger)
        if lost:
            return Advice(
                "STRONG_SELL",
                0.8,
                f"Prediction failed — lost the breakout level {trigger:.2f}",
                reasons + [f"spot {underlying:.2f} back through trigger {trigger:.2f}"],
            )

    if pnl <= HARD_STOP_PCT and intact is not True:
        if swing and intact is not False:
            return Advice("HOLD", 0.6, f"Down {abs(pnl):.0f}% on a swing — wait for a real structure break", reasons)
        if intact is None:
            return Advice("HOLD", 0.45, f"Down {abs(pnl):.0f}% but no tape yet — not a panic sell", reasons)
        return Advice("HARD_SELL", 0.9, f"Stop — down {abs(pnl):.0f}% and thesis not intact", reasons)

    if pnl <= HARD_STOP_PCT and intact is True:
        return Advice("HOLD", 0.7, f"Down {abs(pnl):.0f}% but structure still intact — hold (TSLA lesson)", reasons)

    if pnl >= TAKE_PROFIT_PCT and intact is False:
        return Advice("TAKE_PROFIT", 0.8, f"Bank +{pnl:.0f}% — momentum fading vs VWAP", reasons)

    if pnl >= SCALE_OUT_PCT:
        return Advice("SCALE_OUT", 0.65, f"+{pnl:.0f}% — sell half, trail the rest (let the winner run)", reasons)

    if peak_mark and peak_mark > entry:
        run = (peak_mark - entry) / entry * 100.0
        giveback = (peak_mark - mark) / (peak_mark - entry) if peak_mark > entry else 0.0
        if run >= 25 and giveback >= 0.5 and intact is False:
            return Advice("TAKE_PROFIT", 0.75, "Gave back half of the run with weak structure", reasons)
        if run >= 25 and giveback >= 0.75:
            return Advice("TAKE_PROFIT", 0.8, "Gave back 75% of the run — bank it", reasons)

    if intact is False and pnl < SPREAD_NOISE_PCT:
        return Advice("STRONG_SELL", 0.75, "Thesis broken and trade is red — exit, prediction failed", reasons)

    if pnl < 0 and intact is True:
        return Advice("HOLD", 0.75, "Red mark, thesis alive — do not panic sell", reasons)
    return Advice("HOLD", 0.55, "Hold — no exit trigger", reasons)
