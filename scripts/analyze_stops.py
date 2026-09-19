"""Two questions the headline numbers raise.

1. hard_stop loses $218 a trade while every other exit averages -$13 to -$40.
   Is that damage concentrated before the 2026-09-11 stop fixes, or ongoing?

2. Today three trades armed the new breakeven floor and STILL closed red. The
   floor is evaluated on the mark (the mid) but the fill is the bid, so the
   question is how wide that gap actually is.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)
rows = [r for r in book.get("trades", []) if r.get("status") == "closed" and r.get("opened_at")]
for r in rows:
    r["day"] = str(r["opened_at"])[:10]
    r["dollars"] = float(r.get("pnl_dollars") or 0.0)

print("=== hard_stop damage by day ===")
print("  day          n   total $   avg $   worst pnl%")
by_day = defaultdict(list)
for r in rows:
    if str(r.get("reason")) == "hard_stop":
        by_day[r["day"]].append(r)
for d in sorted(by_day):
    grp = by_day[d]
    worst = min(float(x.get("pnl_pct") or 0) for x in grp)
    print(f"  {d}  {len(grp):>3}  {sum(x['dollars'] for x in grp):>+8.0f}  "
          f"{mean([x['dollars'] for x in grp]):>+7.0f}   {worst:>+6.1f}%")

print("\n=== how deep do hard_stops actually go past the -25% plan? ===")
deep = sorted(
    (r for r in rows if str(r.get("reason")) == "hard_stop" and r.get("pnl_pct") is not None),
    key=lambda r: float(r["pnl_pct"]),
)
for r in deep[:10]:
    print(f"  {r['day']}  {str(r.get('ticker')):<6} entry {r.get('entry'):<6} "
          f"exit {float(r['pnl_pct']):>+7.1f}%  {r['dollars']:>+7.0f}")

print("\n=== bid vs mark: what the floor thinks vs what it fills ===")
gaps = []
for r in rows:
    bid, mark = r.get("bid"), r.get("mark")
    if bid and mark and float(mark) > 0:
        gap = (float(mark) - float(bid)) / float(mark) * 100.0
        if 0 <= gap < 60:
            gaps.append({"t": r.get("ticker"), "gap": gap, "entry": r.get("entry")})
if gaps:
    g = sorted(x["gap"] for x in gaps)
    print(f"  n={len(g)}  median {g[len(g) // 2]:.1f}%   mean {mean(g):.1f}%   "
          f"p75 {g[int(len(g) * 0.75)]:.1f}%   p90 {g[int(len(g) * 0.9)]:.1f}%")
    print("  -> a floor set on the mid needs at least this much headroom to fill green")

print("\n=== today's armed-but-still-red closes ===")
for r in rows:
    if r["day"] != "2026-09-14" or not r.get("peak_mark") or not r.get("entry"):
        continue
    run = (float(r["peak_mark"]) - float(r["entry"])) / float(r["entry"]) * 100.0
    pnl = float(r.get("pnl_pct") or 0)
    if run >= 10 and pnl < 0:
        print(f"  {str(r.get('ticker')):<6} peak {run:>+5.1f}%  exit {pnl:>+6.1f}%  "
              f"mark={r.get('mark')} bid={r.get('bid')}  {r.get('reason')}  {str(r.get('closed_at'))[11:19]}")
