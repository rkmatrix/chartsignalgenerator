"""What is actually being lost to the $225 risk cap?

The cap refuses any contract whose premium x stop% exceeds the budget, which at
a 25% stop means anything over $9.00. Two questions decide whether budget-aware
stop sizing is worth building:

  1. How much P&L sits in the band the cap currently refuses?
  2. Is SPX reachable at any stop that is not inside the spread?
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BUDGET = 225.0
SPREAD_FLOOR_PCT = 8.0

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)
rows = [
    r for r in book["trades"]
    if r.get("status") == "closed" and r.get("entry") and r.get("pnl_dollars") is not None
]
for r in rows:
    r["e"] = float(r["entry"])
    r["d"] = float(r["pnl_dollars"])

print("=== P&L by entry premium band ===")
print(f"  {'band':<16} {'n':>4} {'win%':>6} {'total $':>10} {'avg $':>8}   cap verdict")
bands = [(0, 0.20), (0.20, 1), (1, 2), (2, 5), (5, 9), (9, 15), (15, 22.5), (22.5, 10_000)]
for lo, hi in bands:
    grp = [r for r in rows if lo <= r["e"] < hi]
    if not grp:
        continue
    wins = sum(1 for r in grp if r["d"] > 0)
    # Required stop to keep one contract inside the budget.
    mid_prem = mean([r["e"] for r in grp])
    need = BUDGET / mid_prem if mid_prem > 0 else 999
    verdict = (
        "refused: too cheap" if hi <= 0.20
        else "allowed at 25% stop" if hi <= 9
        else f"needs {need:.0f}% stop" + (" (inside spread)" if need < SPREAD_FLOOR_PCT else " - viable")
    )
    label = f"${lo:g}-${hi:g}" if hi < 10_000 else f"${lo:g}+"
    print(f"  {label:<16} {len(grp):>4} {wins / len(grp) * 100:>5.0f}% "
          f"{sum(r['d'] for r in grp):>+10.0f} {mean([r['d'] for r in grp]):>+8.0f}   {verdict}")

print("\n=== the band budget-aware sizing would unlock ($9.00-$22.50) ===")
band = [r for r in rows if 9 <= r["e"] < 22.5]
if band:
    print(f"  n={len(band)}  total ${sum(r['d'] for r in band):+,.0f}")
    for r in sorted(band, key=lambda r: r["d"]):
        print(f"    {str(r.get('opened_at'))[:10]}  {str(r.get('ticker')):<6} "
              f"${r['e']:>6.2f}  {str(r.get('reason') or ''):<16} {r['d']:>+8.0f}")
else:
    print("  nothing in the book has ever landed in this band")

print("\n=== SPX specifically ===")
spx = [r for r in rows if str(r.get("ticker") or "").upper() == "SPX"]
print(f"  n={len(spx)}  total ${sum(r['d'] for r in spx):+,.0f}  "
      f"median premium ${sorted(r['e'] for r in spx)[len(spx) // 2]:.2f}")
quoted = [r for r in spx if r["d"] != 0]
print(f"  of those, {len(quoted)} actually moved: ${sum(r['d'] for r in quoted):+,.0f}")
print(f"  stop needed to fit ${BUDGET:.0f}: "
      f"{BUDGET / (sorted(r['e'] for r in spx)[len(spx) // 2]):.1f}%  "
      f"(spread noise floor is {SPREAD_FLOOR_PCT:.0f}%)")

print("\n=== SPY, the same bet at a tradeable size ===")
spy = [r for r in rows if str(r.get("ticker") or "").upper() == "SPY"]
if spy:
    med = sorted(r["e"] for r in spy)[len(spy) // 2]
    print(f"  n={len(spy)}  total ${sum(r['d'] for r in spy):+,.0f}  median premium ${med:.2f}")
    print(f"  risk at a 25% stop: ${med * 25:.0f} — inside the ${BUDGET:.0f} budget")
