"""Are givebacks gradual or single-tick? The fix differs completely.

MSFT on 2026-09-21 armed a run floor at +18%, sat at +33% on one poll and sold
at -2.6% on the next, 25 seconds later. No floor level would have caught that:
the move happened between two observations. META overshot a -25% plan stop to
-27.7% the same way.

If most givebacks look like MSFT -- one tick from green to red -- then tightening
the floor is useless and the only lever is how often the desk looks. If most are
gradual, with several polls spent sliding down from the peak, then the floor is
simply set too loose and the desk is watching the decline without acting.

Every trade records its quote path, so count them.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"

sys.path.insert(0, str(ROOT / "src"))
from pa.babysitter.advise import (  # noqa: E402
    BREAKEVEN_ARM_PCT, BREAKEVEN_FLOOR_PCT, RATCHET_KEEP,
)

# The threshold the floor switched on at when these trades were live, so the
# replay reflects what actually happened rather than today's rule.
RATCHET_ARM_PCT = 30.0


def parse_marks(t: dict) -> list[tuple[str, float, float | None]]:
    out = []
    for m in t.get("marks") or []:
        if isinstance(m, (list, tuple)) and len(m) >= 2:
            ts = str(m[0])
            mark = m[1]
            bid = m[2] if len(m) > 2 else None
            if mark is None:
                continue
            out.append((ts, float(mark), None if bid is None else float(bid)))
    return out


def floor_for(run: float) -> float | None:
    if run < BREAKEVEN_ARM_PCT:
        return None
    if run >= RATCHET_ARM_PCT:
        return run * RATCHET_KEEP
    return BREAKEVEN_FLOOR_PCT


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    trades = [t for t in book.get("trades") or []
              if t.get("status") == "closed" and t.get("quote_source") == "uw"
              and t.get("entry") and (t.get("marks") or [])]
    print(f"{len(trades)} closed live-feed trades with a recorded quote path\n")

    gaps, kinds = [], Counter()
    overshoot = []
    detail = []

    for t in trades:
        entry = float(t["entry"])
        path = parse_marks(t)
        if len(path) < 2:
            continue

        # Poll spacing, in seconds, from the recorded timestamps.
        for a, b in zip(path, path[1:]):
            try:
                ha, ma, sa = (int(x) for x in a[0].split(":"))
                hb, mb, sb = (int(x) for x in b[0].split(":"))
                gaps.append((hb * 3600 + mb * 60 + sb) - (ha * 3600 + ma * 60 + sa))
            except ValueError:
                pass

        # Walk the path the way the desk does and find where a floor would arm.
        peak = entry
        armed_at = None
        for i, (ts, mark, bid) in enumerate(path):
            peak = max(peak, mark)
            run = (peak - entry) / entry * 100.0
            fl = floor_for(run)
            sellable = ((bid if bid is not None else mark) - entry) / entry * 100.0
            if fl is not None and sellable <= fl:
                armed_at = (i, ts, sellable, fl, run)
                break

        if armed_at is None:
            continue
        i, ts, sellable, fl, run = armed_at
        # The tick before the trigger: where the floor "should" have sold.
        prev = path[i - 1] if i > 0 else path[0]
        prev_sellable = ((prev[2] if prev[2] is not None else prev[1]) - entry) / entry * 100.0
        miss = prev_sellable - sellable
        overshoot.append({"tk": t.get("ticker"), "floor": fl, "run": run,
                          "prev": prev_sellable, "got": sellable, "miss": miss})

        # Gradual means the desk saw at least one poll between the floor level
        # and the price it actually got; single-tick means it never did.
        if prev_sellable > fl and sellable < fl - 5.0:
            kinds["single tick straight through the floor"] += 1
        elif sellable >= fl - 5.0:
            kinds["caught near the floor"] += 1
        else:
            kinds["gradual slide"] += 1
        detail.append((t.get("ticker"), run, fl, prev_sellable, sellable, miss))

    if gaps:
        print("=== how often does the desk actually look? ===")
        print(f"  {len(gaps)} intervals between quotes")
        print(f"  median {median(gaps):.0f}s, mean {mean(gaps):.0f}s, "
              f"p90 {sorted(gaps)[int(len(gaps) * 0.9)]:.0f}s, max {max(gaps):.0f}s")
        print(f"  configured poll is 15s\n")

    print("=== when the floor triggers, what does it get? ===")
    for kind, n in kinds.most_common():
        print(f"  {n:>4}  {kind}")

    if overshoot:
        misses = [o["miss"] for o in overshoot]
        print(f"\n  {len(overshoot)} floor triggers")
        print(f"  mean overshoot past the floor : {mean(o['floor'] - o['got'] for o in overshoot):+.1f} points")
        print(f"  mean drop in the final tick   : {mean(misses):.1f} points")
        print(f"  median drop in the final tick : {median(misses):.1f} points")

        print("\n  worst cases (floor level vs what the exit actually got):")
        for tk, run, fl, prev, got, miss in sorted(detail, key=lambda d: d[4])[:12]:
            print(f"    {str(tk):<6} ran +{run:>5.1f}%  floor {fl:>+6.1f}%  "
                  f"last seen {prev:>+7.1f}%  sold {got:>+7.1f}%  (dropped {miss:.1f} in one tick)")

    print("\n=== what a faster poll would be worth ===")
    print("  If a drop is spread evenly over the interval, halving the interval")
    print("  halves the distance price can travel unobserved:")
    if overshoot:
        cur = mean(o["floor"] - o["got"] for o in overshoot)
        for div, label in ((1, "15s (configured)"), (2, "7s"), (3, "5s")):
            print(f"    poll {label:<18} mean overshoot about {cur / div:+.1f} points")


if __name__ == "__main__":
    main()
