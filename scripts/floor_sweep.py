"""Where should the breakeven floor arm, and what would it recover?

34 of the 77 live-feed trades went green and closed red, handing back $1,608.
Their peak averaged only +11.0% against a 10% arming threshold, so most were
never protected at all -- the floor exists but sits above where these trades
actually topped out.

Lowering it is not free. SPREAD_NOISE_PCT is -8%, meaning a "run" smaller than
about 8% can just be the mid drifting inside the bid/ask, and arming on noise
sells healthy trades early for a scratch. So sweep it: for each arm level, count
the trades rescued, the dollars recovered, and the dollars given up by cutting
winners that would have gone on to do better.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"
sys.path.insert(0, str(ROOT / "src"))

from pa.babysitter.advise import BREAKEVEN_FLOOR_PCT, RATCHET_KEEP  # noqa: E402

# The threshold the old floor switched on at. Kept here, not imported, because
# this script exists to compare against it and the rule no longer has a cliff.
RATCHET_ARM_PCT = 30.0


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    rows = []
    for t in book.get("trades") or []:
        if t.get("status") != "closed" or t.get("quote_source") != "uw":
            continue
        entry, pnl, peak = t.get("entry"), t.get("pnl_pct"), t.get("peak_mark")
        if entry is None or pnl is None or peak is None:
            continue
        try:
            entry, pnl, peak = float(entry), float(pnl), float(peak)
        except (TypeError, ValueError):
            continue
        if entry <= 0:
            continue
        rows.append({"tk": str(t.get("ticker") or ""), "entry": entry, "pnl": pnl,
                     "run": (peak - entry) / entry * 100.0,
                     "reason": str(t.get("reason") or "")})

    print(f"{len(rows)} closed live-feed trades with a recorded peak\n")

    greens = [r for r in rows if r["run"] > 0]
    roundtrips = [r for r in greens if r["pnl"] <= 0]
    print(f"  went green at some point : {len(greens)}")
    print(f"  of those, closed red     : {len(roundtrips)}")
    runs = sorted(r["run"] for r in roundtrips)
    print(f"  their peaks: median {median(runs):+.1f}%, mean {mean(runs):+.1f}%")
    print("  peak distribution of the round-trippers:")
    for lo, hi in ((0, 5), (5, 8), (8, 10), (10, 15), (15, 25), (25, 1e9)):
        n = sum(1 for r in runs if lo <= r < hi)
        label = f"+{lo:.0f}% to +{hi:.0f}%" if hi < 1e9 else f"over +{lo:.0f}%"
        bar = "#" * n
        print(f"    {label:<16} {n:>3}  {bar}")

    # A peak smaller than the round trip was never money. Entry lifts the ask and
    # the exit hits the bid, so a contract that ticked up 3% on a 4% spread was
    # underwater the whole time it looked green. Measured median round trip on
    # these rows is about 4%, so only peaks above that are real opportunity.
    ROUND_TRIP = 4.0
    real = [r for r in roundtrips if r["run"] > ROUND_TRIP]
    fake = [r for r in roundtrips if r["run"] <= ROUND_TRIP]
    print(f"\n  of the {len(roundtrips)} round-trippers, {len(fake)} peaked at or under "
          f"the {ROUND_TRIP:.0f}% round trip")
    print(f"  -> they never actually offered a profit; only {len(real)} were real")

    print("\n=== sweep: arm the floor at ... ===")
    print("  'rescued' counts red closes the floor would have turned green.")
    print("  A trade that closed above its floor never touched it, so it costs nothing;")
    print("  the only real cost is a trade sold AT the floor that would have recovered,")
    print("  which needs the quote path to see and only 18 rows have one.")
    print(f"\n  {'arm':>5} {'rescued':>8} {'recovered $':>12} {'of which real':>14}")
    base = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 for r in rows)
    for arm in (3.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0):
        rescued = 0
        recovered = 0.0
        real_recovered = 0.0
        for r in rows:
            if r["run"] < arm or r["pnl"] >= 0:
                continue
            floor = r["run"] * RATCHET_KEEP if r["run"] >= RATCHET_ARM_PCT else BREAKEVEN_FLOOR_PCT
            if r["pnl"] >= floor:
                continue
            rescued += 1
            delta = (floor - r["pnl"]) / 100.0 * r["entry"] * 100.0
            recovered += delta
            if r["run"] > ROUND_TRIP:
                real_recovered += delta
        print(f"  {arm:>4.0f}% {rescued:>8} {recovered:>+12.0f} {real_recovered:>+14.0f}")

    print(f"\n  (book total as traded: ${base:,.0f})")
    print("\n  Read the last column, not the third. Recovering dollars on a trade")
    print("  that only ever ran 3% is an artefact of pricing the floor off the mid;")
    print("  at the bid that trade had nothing to protect.")

    # The floor has a cliff in it. Below RATCHET_ARM_PCT it defends a flat +3%
    # regardless of how far the trade ran, and at exactly 30% it jumps to half
    # the run. So a trade that peaked +29% is defended at +3% -- 90% of the run
    # handed back -- while +30% is defended at +15%. Applying the same keep
    # fraction across the whole armed range removes the cliff, and is the rule
    # already agreed for big runs rather than a new parameter fitted here.
    print("\n=== the floor's discontinuity ===")
    print(f"  {'peak run':>10} {'floor now':>11} {'floor if continuous':>21}")
    for run in (10.0, 15.0, 20.0, 25.0, 29.9, 30.0, 40.0, 60.0):
        now = run * RATCHET_KEEP if run >= RATCHET_ARM_PCT else BREAKEVEN_FLOOR_PCT
        cont = max(BREAKEVEN_FLOOR_PCT, run * RATCHET_KEEP)
        print(f"  {run:>9.1f}% {now:>+10.1f}% {cont:>+20.1f}%")

    print("\n=== what the continuous floor is worth, at the current 10% arm ===")
    for label, fn in (
        ("floor as it is today", lambda run: run * RATCHET_KEEP if run >= RATCHET_ARM_PCT else BREAKEVEN_FLOOR_PCT),
        ("continuous ratchet   ", lambda run: max(BREAKEVEN_FLOOR_PCT, run * RATCHET_KEEP)),
    ):
        rescued = 0
        recovered = 0.0
        for r in rows:
            if r["run"] < 10.0 or r["pnl"] >= 0:
                continue
            floor = fn(r["run"])
            if r["pnl"] >= floor:
                continue
            rescued += 1
            recovered += (floor - r["pnl"]) / 100.0 * r["entry"] * 100.0
        print(f"  {label}  rescued {rescued:>3}   recovered ${recovered:>+7.0f}")

    print("\n=== how the round-trippers died ===")
    from collections import Counter
    c = Counter(r["reason"] for r in roundtrips)
    for reason, n in c.most_common():
        d = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 for r in roundtrips if r["reason"] == reason)
        print(f"  {reason:<20} {n:>3}  ${d:>+8.0f}")


if __name__ == "__main__":
    main()
