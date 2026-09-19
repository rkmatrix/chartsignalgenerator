"""Is the first-hour TAKE ban actually holding, or still leaking?

allows_take() refuses TAKE in the first hour, but the book holds 31 first-hour
TAKE rows worth -$3,051. Either the ban arrived after those rows were written,
or it is not being enforced on the live path.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)
trades = book["trades"]


def money(rows: list[dict]) -> float:
    return sum(float(r.get("pnl_dollars") or 0) for r in rows)


rows = [
    r for r in trades
    if str(r.get("verdict", "")).upper() == "TAKE"
    and str(r.get("window", "")) == "first_hour"
]
by_day = defaultdict(list)
for r in rows:
    by_day[str(r.get("opened_at"))[:10]].append(r)

print("first_hour TAKE rows by day (should stop once the ban landed):")
for d in sorted(by_day):
    g = by_day[d]
    print(f"  {d}  n={len(g):<3} {money(g):>+9.0f}")

print("\nevery row with entry premium >= $15 (risk cap now refuses above ~$9):")
big = sorted((r for r in trades if float(r.get("entry") or 0) >= 15), key=lambda r: -float(r["entry"]))
for r in big:
    print(f"  {str(r.get('opened_at'))[:10]}  {str(r.get('ticker')):<6} "
          f"entry ${float(r['entry']):>6.2f}  {str(r.get('verdict')):<6} "
          f"{str(r.get('reason') or ''):<16} {float(r.get('pnl_dollars') or 0):>+9.0f}")
print(f"\n  {len(big)} rows, {money(big):+,.0f} total")

print("\nsanity: what does the live gate say for a first-hour signal today?")
sys.path.insert(0, r"C:\Projects\trading\PA\src")
from pa.open_session.calibrate import allows_take  # noqa: E402

ok, why = allows_take(
    window="first_hour", direction="call", strategies=["orb", "ema_align"], state=None
)
print(f"  allows_take(first_hour) -> {ok}  ({why})")
