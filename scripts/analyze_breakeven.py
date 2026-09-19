"""How much has been given back by letting trades that ran finish red?

Every closed row carries peak_mark, so the run each trade actually saw is known.
This sweeps the arm threshold to pick one from evidence rather than taste.

What this CAN measure: losses that a breakeven floor would have converted to
roughly scratch.

What it CANNOT measure: winners the floor would have cut short, because the book
stores only peak_mark and not the low-water mark, so a trade that dipped to
breakeven and recovered is invisible here. Treat the upside as an upper bound.

Usage: python scripts/analyze_breakeven.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)
rows = [
    r for r in book.get("trades", [])
    if r.get("status") == "closed"
    and r.get("entry")
    and r.get("peak_mark")
    and r.get("pnl_pct") is not None
]
print(f"{len(rows)} closed rows carry a peak\n")

ran_and_lost = []
for r in rows:
    entry = float(r["entry"])
    run = (float(r["peak_mark"]) - entry) / entry * 100.0
    pnl = float(r["pnl_pct"])
    if run > 0 and pnl < 0:
        ran_and_lost.append({
            "ticker": r.get("ticker"),
            "run": run,
            "pnl": pnl,
            "dollars": float(r.get("pnl_dollars") or 0.0),
            "reason": r.get("reason"),
            "day": str(r.get("opened_at") or "")[:10],
        })

ran_and_lost.sort(key=lambda x: x["dollars"])
print(f"{len(ran_and_lost)} trades went green and still closed red, "
      f"costing ${sum(x['dollars'] for x in ran_and_lost):,.0f}\n")

print("worst 12:")
print(f"  {'ticker':<7} {'day':<11} {'peak':>7} {'exit':>8} {'$':>7}  reason")
for x in ran_and_lost[:12]:
    print(f"  {x['ticker']:<7} {x['day']:<11} {x['run']:>+6.1f}% {x['pnl']:>+7.1f}% "
          f"{x['dollars']:>+7.0f}  {x['reason']}")

print("\n=== which exit path lets a green trade finish red? ===")
by_reason: dict[str, list[dict]] = {}
for x in ran_and_lost:
    by_reason.setdefault(str(x["reason"]), []).append(x)
for reason, grp in sorted(by_reason.items(), key=lambda kv: sum(x["dollars"] for x in kv[1])):
    print(f"  {reason:<20} n={len(grp):<4} ${sum(x['dollars'] for x in grp):>8,.0f}")

print("\n=== arm threshold sweep ===")
print("  arm    trades caught   recovered $   (exiting at scratch instead)")
for arm in (5, 8, 10, 12, 15, 20, 25, 30, 40):
    caught = [x for x in ran_and_lost if x["run"] >= arm]
    print(f"  {arm:>3}%   {len(caught):>10}   {-sum(x['dollars'] for x in caught):>+11,.0f}")

print("\n=== how big were the runs that then died? ===")
for lo, hi in ((0, 5), (5, 10), (10, 15), (15, 25), (25, 50), (50, 10_000)):
    grp = [x for x in ran_and_lost if lo <= x["run"] < hi]
    if grp:
        print(f"  run {lo:>3}-{hi if hi < 10_000 else '+':<4} n={len(grp):<4} "
              f"${sum(x['dollars'] for x in grp):>8,.0f}")
