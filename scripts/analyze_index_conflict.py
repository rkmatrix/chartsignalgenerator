"""Is the desk holding an index call and an index put at the same time?

Right now it is: QQQ call and DIA put are both open, and SPY put overlapped a
QQQ call earlier. crowding_block caps MAX_INDEX_PER_SIDE per side, so one call
and one put both pass it - the cap never asks whether the two sides contradict.

SPY, QQQ, DIA and IWM track each other closely enough that holding opposite
sides of them is not a hedge and not two ideas. One of the pair is wrong by
construction, and the round trip gets paid on both.

Measure how often it happens and what the pairs cost.

Usage: python scripts/analyze_index_conflict.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INDEX = {"SPY", "SPX", "QQQ", "DIA", "IWM"}
book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)["trades"]


def when(v):
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


rows = []
for r in book:
    tk = str(r.get("ticker") or "").upper()
    if tk not in INDEX or not r.get("opened_at"):
        continue
    o = when(r["opened_at"])
    c = when(r.get("closed_at")) or o
    if not o:
        continue
    rows.append({
        "tk": tk, "dir": str(r.get("direction") or "").lower(),
        "open": o, "close": c, "day": str(r["opened_at"])[:10],
        "usd": float(r.get("pnl_dollars") or 0.0),
        "closed": r.get("status") == "closed",
    })
rows.sort(key=lambda x: x["open"])

conflicts = []
for i, a in enumerate(rows):
    for b in rows[i + 1:]:
        if b["day"] != a["day"] or b["dir"] == a["dir"]:
            continue
        if b["open"] > a["close"]:  # no overlap in time
            continue
        conflicts.append((a, b))

print(f"{len(rows)} index positions in the book, {len(conflicts)} opposing pairs overlapped\n")
if not conflicts:
    print("no contradictory index pairs found")
    raise SystemExit

days = sorted({a["day"] for a, _ in conflicts})
print(f"happened on {len(days)} of {len({r['day'] for r in rows})} sessions\n")

print(f"  {'day':<12}{'a':<22}{'b':<22}{'pair $':>10}")
total = 0.0
for a, b in conflicts:
    if not (a["closed"] and b["closed"]):
        continue
    pair = a["usd"] + b["usd"]
    total += pair
    print(f"  {a['day']:<12}{a['tk'] + ' ' + a['dir']:<10}{a['usd']:>+8.2f}  "
          f"{b['tk'] + ' ' + b['dir']:<10}{b['usd']:>+8.2f}  {pair:>+10.2f}")

closed_pairs = [(a, b) for a, b in conflicts if a["closed"] and b["closed"]]
print(f"\n  {len(closed_pairs)} fully closed pairs, net ${total:+.2f}")
if closed_pairs:
    both_lost = sum(1 for a, b in closed_pairs if a["usd"] < 0 and b["usd"] < 0)
    print(f"  pairs where BOTH legs lost: {both_lost}/{len(closed_pairs)}")
    print(f"  average per pair: ${total / len(closed_pairs):+.2f}")

print("\n=== what the desk would keep if it took only the first of each pair ===")
drop = {id(b) for _, b in conflicts}
kept = [r for r in rows if r["closed"] and id(r) not in drop]
alldone = [r for r in rows if r["closed"]]
print(f"  all index trades   n={len(alldone):<4} ${sum(r['usd'] for r in alldone):+.2f}")
print(f"  first-of-pair only n={len(kept):<4} ${sum(r['usd'] for r in kept):+.2f}")
