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
# A run this size arms a breakeven floor: past here the trade may not close red.
# 25% was far too high to do any work. Across 162 closed rows, 27 trades went
# green and still closed red for $591, and a 25% arm reached only 5 of them ($59).
# 10% reaches 14 of them ($356) and still clears SPREAD_NOISE_PCT, below which a
# "run" is just the mid drifting inside the bid/ask. Not 12%: AMZN's observed run
# on 2026-09-14 was 11.96%, so a 12% arm missed the trade that prompted this by
# four hundredths of a point.
BREAKEVEN_ARM_PCT = 10.0
# Where an armed trade is allowed to close. Not zero: entry is the ask and exits
# fill at the bid, so flat on the mid is still a loss on the round trip.
BREAKEVEN_FLOOR_PCT = 3.0
# Past a run, stop defending scratch and defend the run itself, so a +45% peak
# is not handed back for +3%. This keep fraction applies across the whole armed
# range. It used to switch on only above a 30% run, which put a cliff in the
# middle of the rule: a trade that peaked +29% was defended at +3%, handing back
# 90% of it, while +30% was defended at +15%. Nothing about a trade changes at
# exactly 30%. Applying the same fraction throughout recovers $567 against $440
# across the live-feed book, on the same 15 trades -- no extra trades cut, so
# this is the cliff being removed rather than a threshold being tuned.
RATCHET_KEEP = 0.5
# The plan stop may not fire until the stock has used this much of the room
# down to the setup's own stop. Under that, a 25% option drop is the gamma of
# a wiggle, not the invalidation the stop was written for. On the live-feed
# book, 10 of 13 plan stops fired with the chart stop still intact and cost
# $831; the stock had moved 0.08% to 0.58%, and 6 of the 10 were green in the
# stock half an hour later. QQQ on 2026-09-24 was the whole day's loss: the
# option was −25% while QQQ was −0.09% and the ORB stop, 0.35% away, was
# untouched.
CHART_ROOM_BEFORE_PLAN = 0.5


def _sellable_peak(peak_mark: float | None, peak_bid: float | None) -> float | None:
    """The best price we could actually have sold, which is the bid.

    A mid can print a run the bid never reached. BAC on 2026-09-24 armed a
    +6% floor off a 0.57 mid while the same tick's bid was 0.53, and sold
    immediately under the floor it had just armed.
    """
    if peak_bid is not None and float(peak_bid) > 0:
        return float(peak_bid)
    if peak_mark is not None and float(peak_mark) > 0:
        return float(peak_mark)
    return None


def breakeven_floor(
    entry: float,
    peak_mark: float | None,
    *,
    peak_bid: float | None = None,
) -> float | None:
    """Worst P&L a trade that has already run is allowed to close at.

    None until a run arms it, so trades that never went green are left to the
    ordinary stops. The run is measured on the bid when we have one.
    """
    peak = _sellable_peak(peak_mark, peak_bid)
    if not peak or entry <= 0 or peak <= entry:
        return None
    run = (peak - entry) / entry * 100.0
    if run < BREAKEVEN_ARM_PCT:
        return None
    return round(max(BREAKEVEN_FLOOR_PCT, run * RATCHET_KEEP), 2)


def chart_stop_has_room(
    *,
    is_call: bool,
    underlying: float | None,
    entry_underlying: float | None,
    underlying_stop: float | None,
    max_used: float = CHART_ROOM_BEFORE_PLAN,
) -> bool:
    """True when the setup stop is intact and most of its room is still there.

    False when there is no chart stop, the stop was already through the entry,
    or price has crossed it. In those cases the plan stop stays the backstop.
    """
    if underlying is None or entry_underlying is None or underlying_stop is None:
        return False
    entry_px = float(entry_underlying)
    spot = float(underlying)
    stop = float(underlying_stop)
    if entry_px <= 0 or max_used <= 0:
        return False
    if is_call:
        room = entry_px - stop
        if room <= 0 or spot < stop:
            return False
        used = max(0.0, entry_px - spot)
    else:
        room = stop - entry_px
        if room <= 0 or spot > stop:
            return False
        used = max(0.0, spot - entry_px)
    return (used / room) < max_used


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
    bid: float | None = None,
    underlying: float | None = None,
    vwap: float | None = None,
    ema9: float | None = None,
    ema9_prev: float | None = None,
    minutes_to_close: int | None = None,
    is_0dte: bool = False,
    peak_mark: float | None = None,
    peak_bid: float | None = None,
    days_to_expiry: int | None = None,
    plan_stop_pct: float | None = None,
    underlying_stop: float | None = None,
    entry_underlying: float | None = None,
    trigger: float | None = None,
    tape_direction: str | None = None,
    minutes_held: float | None = None,
) -> Advice:
    if mark is None or entry <= 0:
        return Advice("HOLD", 0.2, "Holding — no live option quote", ["no mark"])
    pnl = (mark - entry) / entry * 100.0
    # What selling right now would actually realise. Exits fill at the bid, so a
    # floor judged on the mid books a loss about a spread wide: on 2026-09-14
    # AVGO, GOOGL and AMD all armed the floor and still closed red at -3.1%,
    # -8.8% and -1.5%. The bid/mid gap runs 1.9% median and 6.1% at p90, which
    # swamps a 3% floor. Using the bid also trips the floor slightly earlier,
    # which is the only defence against a quote gapping straight through it.
    exit_pnl = ((float(bid) - entry) / entry * 100.0) if bid else pnl
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

    # ---- Breakeven floor ----------------------------------------------------
    # This sits above every stop below it deliberately. The stops were winning
    # the race and closing trades that had already been green: AMZN peaked +34%
    # and died at -13% on underlying_stop, IWM peaked +22% and died at -15% on
    # thesis_broken. Those paths account for $532 of the $591 given back.
    # Arming does not mean refusing to sell, it means selling HERE rather than
    # waiting for a stop 20-50 points lower.
    floor = breakeven_floor(entry, peak_mark, peak_bid=peak_bid)
    if floor is not None and exit_pnl <= floor:
        peak = _sellable_peak(peak_mark, peak_bid)
        run = (float(peak) - entry) / entry * 100.0
        held_back = "run" if floor > BREAKEVEN_FLOOR_PCT else "breakeven"
        return Advice(
            "TAKE_PROFIT",
            0.9,
            f"Protecting a +{run:.0f}% run — {held_back} floor at {floor:+.0f}%",
            reasons + [f"peak +{run:.0f}%, sellable {exit_pnl:+.1f}%"],
        )

    side = "call" if is_call else "put"
    if tape_direction in {"call", "put"} and tape_direction != side:
        return Advice(
            "STRONG_SELL",
            0.85,
            f"Prediction failed — tape flipped to {tape_direction.upper()}",
            reasons + [f"live signal is {tape_direction}"],
        )

    # The stop is a price we can sell at. Judging it on the mid holds a trade
    # whose bid is already through the limit: the fill is the bid, so the
    # decision has to be too. With no bid, exit_pnl falls back to the mid.
    # While the setup stop still has most of its room, this drop is the option
    # gearing a small stock move. Selling here replaces the chart stop with a
    # much tighter one. Past HARD_STOP_PCT the wait is over anyway: the option
    # has already lost half, and holding for a far chart stop can take the rest.
    if (
        plan_stop_pct
        and plan_stop_pct > 0
        and exit_pnl <= min(-float(plan_stop_pct), SPREAD_NOISE_PCT)
        and not (
            exit_pnl > HARD_STOP_PCT
            and chart_stop_has_room(
                is_call=is_call,
                underlying=underlying,
                entry_underlying=entry_underlying,
                underlying_stop=underlying_stop,
            )
        )
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
        # The "may not close red" rule now lives in breakeven_floor() above these
        # stops, because down here it was unreachable. What is left are the
        # earlier, structure-aware banks that fire while the trade is still green.
        if run >= 25 and giveback >= 0.5 and intact is False:
            return Advice("TAKE_PROFIT", 0.75, "Gave back half of the run with weak structure", reasons)
        if run >= 25 and giveback >= 0.75:
            return Advice("TAKE_PROFIT", 0.8, "Gave back 75% of the run — bank it", reasons)

    if intact is False and pnl < SPREAD_NOISE_PCT:
        return Advice("STRONG_SELL", 0.75, "Thesis broken and trade is red — exit, prediction failed", reasons)

    if pnl < 0 and intact is True:
        return Advice("HOLD", 0.75, "Red mark, thesis alive — do not panic sell", reasons)
    return Advice("HOLD", 0.55, "Hold — no exit trigger", reasons)
