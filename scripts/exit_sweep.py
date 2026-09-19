"""Replay exit rules against reconstructed option paths and sweep their knobs.

Reads data/history/exit_paths.json (see exit_lab.py) and converts each signal's
underlying path into an option path, then runs the desk's own exit state machine
over it under varying parameters.

Two things this is careful about, because both would otherwise manufacture an
answer:

  Theta is assumed, not fitted, and it is assumed CONSERVATIVELY. An ATM option's
  time value scales with sqrt(time remaining), and we apply that to the whole
  premium. Real decay applies only to the time-value portion, which shrinks as a
  winner goes in the money, so this overcharges winners for holding. That biases
  every result AGAINST holding longer. Any rule that still prefers holding under
  this model would look better under a truer one, which is the safe direction to
  be wrong in. --theta scales it so the sensitivity is visible.

  Every sweep is reported in-sample and out-of-sample on a date split. A knob
  tuned on all 25 sessions at once is a knob fitted to noise, which is how the
  breakeven arm got to 10% in the first place.

Usage:
  python scripts/exit_sweep.py                 sweep the breakeven arm
  python scripts/exit_sweep.py --theta 0.5     half the assumed decay
  python scripts/exit_sweep.py --grid          arm x floor
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HIST = Path(r"C:\Projects\trading\PA\data\history")

# The desk's current settings, as the baseline every variant is measured against.
CUR_ARM = 10.0
CUR_FLOOR = 3.0
CUR_RATCHET_ARM = 30.0
CUR_RATCHET_KEEP = 0.5
CUR_PLAN_STOP = 25.0
CUR_TAKE_PROFIT = 75.0
GRACE = 1
EOD_FLATTEN = 20
CONTRACT = 200.0  # $2.00 premium, the middle of the live premium band


def option_path(move_path: list[float], mins_to_close: int, lev: float, rt: float,
                theta_scale: float) -> list[tuple[float, float]]:
    """(mark, sellable) P&L in percent of premium, minute by minute."""
    out = []
    t0 = float(max(1, mins_to_close))
    for i, move in enumerate(move_path, start=1):
        left = max(0.0, t0 - i)
        decay = (math.sqrt(left / t0) - 1.0) * 100.0 * theta_scale
        gross = lev * move + decay
        out.append((gross + rt / 2.0, gross + rt))
    return out


def replay(path: list[tuple[float, float]], mins_to_close: int, *, arm: float, floor: float,
           ratchet_arm: float, ratchet_keep: float, plan_stop: float,
           take_profit: float, max_hold: int | None = None) -> tuple[float, str, int]:
    """Run the exit state machine. Returns (sellable pnl pct, reason, minutes)."""
    peak = 0.0
    for t, (mark, sellable) in enumerate(path, start=1):
        peak = max(peak, mark)
        if t < GRACE:
            continue
        # Breakeven floor first: it sits above the stops in advise.py precisely
        # so a trade that ran is sold HERE rather than at a stop far below.
        if peak >= arm:
            lim = round(peak * ratchet_keep, 2) if peak >= ratchet_arm else floor
            if sellable <= lim:
                return sellable, "floor", t
        if sellable <= -plan_stop:
            return sellable, "plan_stop", t
        if sellable >= take_profit:
            return sellable, "take_profit", t
        if max_hold is not None and t >= max_hold:
            return sellable, "max_hold", t
        if t >= mins_to_close - EOD_FLATTEN:
            return sellable, "eod", t
    return path[-1][1], "ran_out", len(path)


def evaluate(sigs: list[dict], lev: float, rt: float, theta_scale: float, **kw) -> dict:
    tot = 0.0
    wins = 0
    held = 0
    reasons: dict[str, int] = {}
    for s in sigs:
        p = option_path(s["path"], s["mins_to_close"], lev, rt, theta_scale)
        pnl, reason, t = replay(p, s["mins_to_close"], **kw)
        tot += pnl / 100.0 * CONTRACT
        wins += 1 if pnl > 0 else 0
        held += t
        reasons[reason] = reasons.get(reason, 0) + 1
    n = max(1, len(sigs))
    return {"pnl": tot, "win": wins / n * 100.0, "n": len(sigs),
            "held": held / n, "reasons": reasons}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theta", type=float, default=1.0, help="scale the assumed decay")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--cohorts", action="store_true", help="break down by window/verdict")
    ap.add_argument("--verdict", default="", help="restrict to TAKE or WATCH")
    args = ap.parse_args()

    blob = json.loads((HIST / "exit_paths.json").read_text(encoding="utf-8"))
    lev = blob["model"]["lev"]
    rt = blob["model"]["round_trip"]
    sigs = blob["signals"]
    if args.verdict:
        sigs = [s for s in sigs if s["verdict"] == args.verdict.upper()]

    days = sorted({s["day"] for s in sigs})
    cut = days[len(days) // 2]
    a = [s for s in sigs if s["day"] < cut]
    b = [s for s in sigs if s["day"] >= cut]
    print(f"model: {lev:.1f}x leverage, {rt:+.1f}% round trip, theta x{args.theta}")
    print(f"{len(sigs)} signals over {len(days)} sessions"
          f"   split at {cut}: {len(a)} early / {len(b)} late\n")

    base = dict(arm=CUR_ARM, floor=CUR_FLOOR, ratchet_arm=CUR_RATCHET_ARM,
                ratchet_keep=CUR_RATCHET_KEEP, plan_stop=CUR_PLAN_STOP,
                take_profit=CUR_TAKE_PROFIT)

    if args.cohorts:
        for key in ("window", "verdict", "dir"):
            print(f"=== by {key} (under current exit rules) ===")
            print("%-14s %6s %10s %8s %10s %8s"
                  % (key, "n", "early $", "win%", "late $", "win%"))
            for val in sorted({s[key] for s in sigs}):
                ea = [s for s in a if s[key] == val]
                la = [s for s in b if s[key] == val]
                if not la:
                    continue
                ra = evaluate(ea, lev, rt, args.theta, **base) if ea else {"pnl": 0, "win": 0}
                rb = evaluate(la, lev, rt, args.theta, **base)
                print("%-14s %6d %10.0f %7.0f%% %10.0f %7.0f%%"
                      % (val, len(ea) + len(la), ra["pnl"], ra["win"], rb["pnl"], rb["win"]))
            print()
        return

    if args.grid:
        print("=== breakeven arm x floor (out-of-sample half) ===")
        print("%-8s" % "arm\\floor" + "".join("%9.0f%%" % f for f in (0, 3, 5, 8)))
        for arm in (4, 6, 8, 10, 14, 20):
            cells = []
            for fl in (0, 3, 5, 8):
                kw = dict(base, arm=float(arm), floor=float(fl))
                cells.append(evaluate(b, lev, rt, args.theta, **kw)["pnl"])
            print("%-8d" % arm + "".join("%10.0f" % c for c in cells))
        return

    print("=== breakeven arm ===")
    print("%-6s %10s %7s %10s %7s %7s" % ("arm", "early $", "win%", "late $", "win%", "mins"))
    for arm in (3, 4, 5, 6, 8, 10, 12, 15, 20, 30, 999):
        kw = dict(base, arm=float(arm))
        ra = evaluate(a, lev, rt, args.theta, **kw)
        rb = evaluate(b, lev, rt, args.theta, **kw)
        tag = "off" if arm == 999 else str(arm)
        star = "  <- current" if arm == 10 else ""
        print("%-6s %10.0f %6.0f%% %10.0f %6.0f%% %7.0f%s"
              % (tag, ra["pnl"], ra["win"], rb["pnl"], rb["win"], rb["held"], star))

    print("\n=== plan stop ===")
    print("%-6s %10s %7s %10s %7s" % ("stop", "early $", "win%", "late $", "win%"))
    for st in (15, 20, 25, 30, 40, 50):
        kw = dict(base, plan_stop=float(st))
        ra = evaluate(a, lev, rt, args.theta, **kw)
        rb = evaluate(b, lev, rt, args.theta, **kw)
        print("%-6d %10.0f %6.0f%% %10.0f %6.0f%%%s"
              % (st, ra["pnl"], ra["win"], rb["pnl"], rb["win"],
                 "  <- current" if st == 25 else ""))

    print("\n=== max hold (minutes) ===")
    print("%-6s %10s %7s %10s %7s" % ("hold", "early $", "win%", "late $", "win%"))
    for h in (10, 15, 20, 30, 45, 60, 90, 0):
        kw = dict(base, max_hold=(h or None))
        ra = evaluate(a, lev, rt, args.theta, **kw)
        rb = evaluate(b, lev, rt, args.theta, **kw)
        print("%-6s %10.0f %6.0f%% %10.0f %6.0f%%"
              % (h or "none", ra["pnl"], ra["win"], rb["pnl"], rb["win"]))

    r = evaluate(b, lev, rt, args.theta, **base)
    print(f"\nbaseline exits (late half): {r['reasons']}")


if __name__ == "__main__":
    main()
