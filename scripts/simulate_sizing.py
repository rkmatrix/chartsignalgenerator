"""Every row is one contract, so premium decides risk. Should it?

On 2026-09-15 the worst percentage loser (IWM, -29.3%) cost $12 while a milder
one (JPM, -19.8%) cost $95, purely because JPM's contract was twelve times the
price. Dollar outcomes are being set by contract price rather than by signal
quality, and risk_block only refuses the extreme tail rather than levelling it.

Realised pnl_pct does not depend on how many contracts we held, so sizing
policies can be replayed over the book exactly. Compare:

  baseline   one contract, what we do now
  cap N      lower the per-trade risk ceiling, refusing pricier contracts
  equal K    buy enough contracts to reach a target risk, at most K of them

Usage: python scripts/simulate_sizing.py [YYYY-MM-DD cutoff for "clean" set]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Quotes before this date came off the delayed Yahoo chain and misprice fills.
CLEAN_FROM = sys.argv[1] if len(sys.argv) > 1 else "2026-09-10"
MIN_PREMIUM = 0.20

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)["trades"]

rows = []
for r in book:
    if r.get("status") != "closed" or r.get("pnl_pct") is None or not r.get("entry"):
        continue
    entry = float(r["entry"])
    if entry <= 0:
        continue
    rows.append({
        "day": str(r.get("opened_at", ""))[:10],
        "ticker": r.get("ticker", ""),
        "entry": entry,
        "pct": float(r["pnl_pct"]),
        "stop": float(r.get("plan_stop_pct") or 25.0),
    })

rows.sort(key=lambda x: x["day"])
clean = [r for r in rows if r["day"] >= CLEAN_FROM]


def risk_per_contract(r: dict) -> float:
    return r["entry"] * 100.0 * r["stop"] / 100.0


def run(rs: list[dict], cap: float, target: float | None, max_ct: int) -> tuple[float, int, int]:
    """Total dollars, trades taken, winners."""
    total = 0.0
    taken = winners = 0
    for r in rs:
        if r["entry"] < MIN_PREMIUM:
            continue
        risk = risk_per_contract(r)
        if risk > cap:
            continue
        ct = 1
        if target:
            ct = max(1, min(max_ct, int(round(target / risk)) if risk > 0 else 1))
        pnl = ct * r["entry"] * 100.0 * r["pct"] / 100.0
        total += pnl
        taken += 1
        winners += 1 if r["pct"] > 0 else 0
    return total, taken, winners


def show(label: str, rs: list[dict], cap: float, target: float | None, max_ct: int) -> None:
    total, taken, win = run(rs, cap, target, max_ct)
    if not taken:
        print(f"  {label:<30} no trades")
        return
    print(f"  {label:<30} ${total:>9.2f}   n={taken:<4} win={win / taken * 100:>4.1f}%  "
          f"per trade ${total / taken:>+7.2f}")


for name, rs in (("ALL CLOSED", rows), (f"CLEAN (from {CLEAN_FROM})", clean)):
    print(f"\n=== {name} — {len(rs)} trades ===")
    show("baseline (1 contract, cap 225)", rs, 225.0, None, 1)
    for cap in (150.0, 100.0, 75.0, 50.0):
        show(f"one contract, cap ${cap:.0f}", rs, cap, None, 1)
    for target in (50.0, 75.0, 100.0):
        for mx in (3, 5):
            show(f"equal-risk ${target:.0f}, max {mx} ct", rs, 225.0, target, mx)

print("\n=== where the dollars sit, by entry premium (clean set) ===")
bands = [(0.20, 1.0), (1.0, 2.0), (2.0, 3.5), (3.5, 6.0), (6.0, 999.0)]
for lo, hi in bands:
    grp = [r for r in clean if lo <= r["entry"] < hi]
    if not grp:
        continue
    tot = sum(r["entry"] * 100.0 * r["pct"] / 100.0 for r in grp)
    win = sum(1 for r in grp if r["pct"] > 0)
    avg_pct = sum(r["pct"] for r in grp) / len(grp)
    print(f"  ${lo:>5.2f}-{hi:<6.2f} n={len(grp):<4} win={win / len(grp) * 100:>4.1f}%  "
          f"avg={avg_pct:>+6.2f}%  dollars=${tot:>+9.2f}")
