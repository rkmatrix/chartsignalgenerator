"""What has actually been happening, day by day, and where the money goes.

Accuracy alone does not decide whether a book makes money: a 40% strategy that
wins 2x what it loses beats a 60% one that loses 2x what it wins. So this prints
win rate AND expectancy side by side, plus the exit paths and setups behind them.

Usage: python scripts/analyze_recent.py [n_days]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

N_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)
rows = [
    r for r in book.get("trades", [])
    if r.get("status") == "closed" and r.get("pnl_pct") is not None and r.get("opened_at")
]
for r in rows:
    r["day"] = str(r["opened_at"])[:10]
    r["dollars"] = float(r.get("pnl_dollars") or 0.0)
    r["pct"] = float(r["pnl_pct"])
rows.sort(key=lambda r: r["opened_at"])
days = sorted({r["day"] for r in rows})[-N_DAYS:]
rows = [r for r in rows if r["day"] in days]


def expectancy(grp: list[dict]) -> str:
    """Win rate is only half the story; this is the half that pays."""
    if not grp:
        return "n=0"
    wins = [r["dollars"] for r in grp if r["dollars"] > 0]
    losses = [r["dollars"] for r in grp if r["dollars"] <= 0]
    wr = len(wins) / len(grp) * 100.0
    aw = mean(wins) if wins else 0.0
    al = mean(losses) if losses else 0.0
    exp = sum(r["dollars"] for r in grp) / len(grp)
    return (
        f"n={len(grp):<4} win={wr:>3.0f}%  avgW={aw:>+7.0f}  avgL={al:>+7.0f}  "
        f"exp/trade={exp:>+7.1f}  total={sum(r['dollars'] for r in grp):>+8.0f}"
    )


print(f"=== last {len(days)} sessions ===")
for d in days:
    grp = [r for r in rows if r["day"] == d]
    print(f"  {d}  {expectancy(grp)}")

print(f"\n=== whole window ===\n  {expectancy(rows)}")

print("\n=== by verdict ===")
for v in sorted({str(r.get('verdict')) for r in rows}):
    print(f"  {v:<8} {expectancy([r for r in rows if str(r.get('verdict')) == v])}")

print("\n=== by exit reason (sorted by damage) ===")
by_reason = defaultdict(list)
for r in rows:
    by_reason[str(r.get("reason"))].append(r)
for reason, grp in sorted(by_reason.items(), key=lambda kv: sum(x["dollars"] for x in kv[1])):
    print(f"  {reason:<18} {expectancy(grp)}")

print("\n=== by setup (a signal is credited to every setup in its stack) ===")
by_setup = defaultdict(list)
for r in rows:
    for s in (r.get("strategies") or ["(none)"]):
        by_setup[str(s)].append(r)
for s, grp in sorted(by_setup.items(), key=lambda kv: sum(x["dollars"] for x in kv[1])):
    print(f"  {s:<20} {expectancy(grp)}")

print("\n=== does score predict anything? ===")
for lo, hi in ((0, 70), (70, 80), (80, 86), (86, 91), (91, 101)):
    grp = [r for r in rows if r.get("score") is not None and lo <= float(r["score"]) < hi]
    if grp:
        print(f"  score {lo:>3}-{hi - 1:<3} {expectancy(grp)}")

print("\n=== how long do winners and losers live? ===")
for label, grp in (
    ("winners", [r for r in rows if r["dollars"] > 0]),
    ("losers", [r for r in rows if r["dollars"] <= 0]),
):
    if not grp:
        continue
    mins = []
    for r in grp:
        try:
            from datetime import datetime
            o = datetime.fromisoformat(str(r["opened_at"]))
            c = datetime.fromisoformat(str(r["closed_at"]))
            mins.append((c - o).total_seconds() / 60.0)
        except Exception:
            pass
    if mins:
        mins.sort()
        print(f"  {label:<8} median {mins[len(mins) // 2]:>5.1f}m   mean {mean(mins):>5.1f}m")

print("\n=== the give-back problem, still ===")
gb = [
    r for r in rows
    if r.get("peak_mark") and r.get("entry")
    and (float(r["peak_mark"]) - float(r["entry"])) / float(r["entry"]) * 100.0 >= 10
    and r["dollars"] < 0
]
print(f"  ran >=10% then closed red: n={len(gb)}  ${sum(r['dollars'] for r in gb):,.0f}")
for r in gb[-8:]:
    run = (float(r["peak_mark"]) - float(r["entry"])) / float(r["entry"]) * 100.0
    print(f"    {r['day']}  {str(r.get('ticker')):<6} peak {run:>+5.1f}%  exit {r['pct']:>+6.1f}%  {r.get('reason')}")
