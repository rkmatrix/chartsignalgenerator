"""Lesson 5.4: should the desk refuse trades that are not worth 2x their risk?

Every row already carries both legs of the ratio. `plan_stop_pct` is what the
trade risks and `take_profit_pct` is what it is aiming for, both as a share of
premium, so reward:risk is simply their quotient. take_profit_pct is scaled by
conviction (25-75) against a fixed 25% stop, which puts the live range at
roughly 1:1 to 1:3 — meaning a 1:2 floor is a real filter, not a no-op.

Measured on realised option P&L, on top of the premium band that is already
live, so this answers "what does the ratio add to what we ship today".

Usage: python scripts/backtest_risk_reward.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.open_session.ledger import risk_block  # noqa: E402

CLEAN_FROM = "2026-09-10"
book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)["trades"]

rows = []
for r in book:
    if r.get("status") != "closed" or r.get("pnl_dollars") is None or not r.get("entry"):
        continue
    stop = float(r.get("plan_stop_pct") or 0)
    tp = float(r.get("take_profit_pct") or 0)
    if stop <= 0 or tp <= 0:
        continue
    banded = risk_block({"entry": float(r["entry"]), "plan_stop_pct": stop}) is None
    rows.append({
        "day": str(r["opened_at"])[:10],
        "rr": tp / stop,
        "usd": float(r["pnl_dollars"]),
        "pct": float(r.get("pnl_pct") or 0),
        "banded": banded,
        "verdict": str(r.get("verdict") or "").upper(),
    })

band = [r for r in rows if r["banded"]]
clean = [r for r in band if r["day"] >= CLEAN_FROM]


def show(label: str, rs: list[dict]) -> None:
    if not rs:
        print(f"  {label:<28} no trades")
        return
    usd = sum(r["usd"] for r in rs)
    win = sum(1 for r in rs if r["usd"] > 0)
    print(f"  {label:<28} ${usd:>9.2f}   n={len(rs):<4} win={win / len(rs) * 100:>4.1f}%   "
          f"${usd / len(rs):>+7.2f}/trade")


print(f"{len(rows)} closed trades carry both legs; {len(band)} also clear the premium band\n")

print("=== reward:risk distribution (band-passing trades) ===")
buckets = [(0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 9.9)]
for lo, hi in buckets:
    grp = [r for r in band if lo <= r["rr"] < hi]
    show(f"{lo:.1f} <= R:R < {hi:.1f}", grp)

for name, rs in (("BAND (all sessions)", band), (f"BAND + live feed ({CLEAN_FROM}+)", clean)):
    print(f"\n=== {name} — {len(rs)} trades ===")
    show("no ratio filter", rs)
    for floor in (1.5, 2.0, 2.5):
        show(f"require R:R >= {floor}", [r for r in rs if r["rr"] >= floor])

print("\n=== does the 2.0 floor hold in both halves? ===")
for name, rs in (("all sessions", band), ("live feed", clean)):
    days = sorted({r["day"] for r in rs})
    if len(days) < 4:
        print(f"  {name}: only {len(days)} sessions, cannot split")
        continue
    mid = days[len(days) // 2]
    for half, sel in (("early", [r for r in rs if r["day"] < mid]),
                      ("late", [r for r in rs if r["day"] >= mid])):
        base = sum(r["usd"] for r in sel)
        filt = sum(r["usd"] for r in sel if r["rr"] >= 2.0)
        n = sum(1 for r in sel if r["rr"] >= 2.0)
        print(f"  {name:<12} {half:<6} ${base:>9.2f} -> ${filt:>9.2f} (n={n:<3})  "
              f"{'better' if filt > base else 'worse'}")

print("\n=== per session, band vs band+R:R>=2.0 (live feed) ===")
by = defaultdict(list)
for r in clean:
    by[r["day"]].append(r)
for d in sorted(by):
    a = sum(x["usd"] for x in by[d])
    b = sum(x["usd"] for x in by[d] if x["rr"] >= 2.0)
    nb = sum(1 for x in by[d] if x["rr"] >= 2.0)
    print(f"  {d}  band ${a:>8.2f} (n={len(by[d]):<3}) -> +R:R ${b:>8.2f} (n={nb})")
