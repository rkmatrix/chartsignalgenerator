"""How much did we sell too early for?

The book records what a trade made. It does not record what the contract went
on to be worth after we let it go, so an exit that fires while the underlying
is still travelling our way looks like a win and never gets questioned.

Reconstruct that: for every closed row, walk the tape from the exit to the
close and price the contract's intrinsic value along the way. Intrinsic is a
floor rather than the true mark, so anything this reports as forgone is real
and the actual number is larger.

Usage: python scripts/analyze_left_on_table.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ET = ZoneInfo("America/New_York")
hist = Path(r"C:\Projects\trading\PA\data\history")

# ticker -> day -> [(dt, close)], RTH only, deduped because the daily dumps
# overlap the history pulls and repeat timestamps.
tape: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
for path in list(hist.glob("*_1m_hist.json")) + list(hist.glob("*_1m_today.json")):
    tk = path.name.split("_")[0].upper()
    try:
        bars = json.loads(path.read_text(encoding="utf-8"))["bars"]
    except Exception:
        continue
    for b in bars:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        if not (9, 30) <= (ts.hour, ts.minute) or ts.hour >= 16:
            continue
        tape[tk][ts.date().isoformat()][ts.strftime("%H:%M")] = float(b["close"])

book = json.loads((hist / "signal_book.json").read_text(encoding="utf-8"))["trades"]

rows = []
for r in book:
    if r.get("status") != "closed" or r.get("pnl_dollars") is None:
        continue
    if not r.get("strike") or not r.get("closed_at") or not r.get("entry"):
        continue
    tk = str(r.get("ticker") or "").upper()
    day = str(r.get("opened_at", ""))[:10]
    if day not in tape.get(tk, {}):
        continue
    try:
        out = datetime.fromisoformat(str(r["closed_at"])).astimezone(ET)
    except Exception:
        continue
    # 0DTE only: a later expiry keeps working past today's tape.
    if str(r.get("expiry") or "")[:10] != day:
        continue

    k = float(r["strike"])
    entry = float(r["entry"])
    is_put = str(r.get("direction") or "").lower().startswith("p")
    after = {t: c for t, c in tape[tk][day].items() if t > out.strftime("%H:%M")}
    if not after:
        continue
    best = max((k - c) if is_put else (c - k) for c in after.values())
    best = max(0.0, best)
    got = float(r["pnl_dollars"])
    could = (best - entry) * 100.0
    rows.append({
        "ticker": tk, "day": day, "reason": r.get("reason", ""),
        "got": got, "could": could, "left": could - got,
        "pct": float(r.get("pnl_pct") or 0),
    })

if not rows:
    print("no 0DTE rows could be matched to the tape")
    raise SystemExit

print(f"matched {len(rows)} closed 0DTE trades to the tape\n")
took = sum(r["got"] for r in rows)
peak = sum(max(r["could"], r["got"]) for r in rows)
print(f"booked            ${took:>10.2f}")
print(f"if each had been sold at its best point after our exit   ${peak:>10.2f}")
print(f"forgone (intrinsic only, so a floor)                      ${peak - took:>10.2f}\n")

print("=== by exit reason ===")
print(f"  {'reason':<20}{'n':>4}{'booked':>11}{'best after':>12}{'left':>11}")
by = defaultdict(list)
for r in rows:
    by[r["reason"]].append(r)
for reason, grp in sorted(by.items(), key=lambda kv: -sum(x["could"] - x["got"] for x in kv[1])):
    g = sum(x["got"] for x in grp)
    c = sum(max(x["could"], x["got"]) for x in grp)
    print(f"  {reason:<20}{len(grp):>4}{g:>11.2f}{c:>12.2f}{c - g:>11.2f}")

print("\n=== sold green, then it doubled or better ===")
big = [r for r in rows if r["got"] > 0 and r["could"] > r["got"] * 2 and r["could"] - r["got"] > 40]
big.sort(key=lambda r: -(r["could"] - r["got"]))
for r in big[:12]:
    print(f"  {r['day']}  {r['ticker']:<6}{r['reason']:<18} "
          f"booked ${r['got']:>7.2f}  reachable ${r['could']:>8.2f}  "
          f"left ${r['could'] - r['got']:>8.2f}")
print(f"  ({len(big)} trades, ${sum(r['could'] - r['got'] for r in big):,.2f} forgone)")
