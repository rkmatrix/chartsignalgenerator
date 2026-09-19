"""Does a premium band survive out of sample, or am I fitting 58 trades?

The clean set says both tails lose: sub-$1 contracts win 29% because their
spread swamps the edge, and $6+ contracts lost $461 across four trades. A band
picked after seeing that is exactly the move that produced the bogus score
cutoff earlier, so it does not get implemented until it holds on data that did
not choose it.

Two independent checks:
  halves     split each sample by date, band must win in both
  bootstrap  resample with replacement, how often does the band beat baseline

Usage: python scripts/validate_premium_band.py [lo] [hi]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LO = float(sys.argv[1]) if len(sys.argv) > 1 else 1.00
HI = float(sys.argv[2]) if len(sys.argv) > 2 else 3.50
CLEAN_FROM = "2026-09-10"
random.seed(7)

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)["trades"]

rows = []
for r in book:
    if r.get("status") != "closed" or r.get("pnl_pct") is None or not r.get("entry"):
        continue
    e = float(r["entry"])
    if e <= 0:
        continue
    rows.append({"day": str(r.get("opened_at", ""))[:10], "entry": e,
                 "pct": float(r["pnl_pct"]),
                 "usd": e * 100.0 * float(r["pnl_pct"]) / 100.0})
rows.sort(key=lambda x: x["day"])


def summarise(rs: list[dict]) -> tuple[float, float, int]:
    if not rs:
        return 0.0, 0.0, 0
    win = sum(1 for r in rs if r["pct"] > 0) / len(rs) * 100.0
    return sum(r["usd"] for r in rs), win, len(rs)


def band(rs: list[dict]) -> list[dict]:
    return [r for r in rs if LO <= r["entry"] <= HI]


def report(label: str, rs: list[dict]) -> None:
    b_usd, b_win, b_n = summarise(rs)
    k_usd, k_win, k_n = summarise(band(rs))
    print(f"  {label:<22} baseline ${b_usd:>9.2f} ({b_win:>4.1f}% of {b_n:<4}) "
          f"->  band ${k_usd:>9.2f} ({k_win:>4.1f}% of {k_n:<4})  "
          f"delta ${k_usd - b_usd:>+9.2f}")


print(f"premium band ${LO:.2f}-${HI:.2f}\n")
clean = [r for r in rows if r["day"] >= CLEAN_FROM]
dirty = [r for r in rows if r["day"] < CLEAN_FROM]

print("=== the band on each sample ===")
report("all closed", rows)
report(f"clean (>={CLEAN_FROM})", clean)
report("pre-UW (unreliable)", dirty)

print("\n=== split by date, band must hold in both halves ===")
for name, rs in (("all closed", rows), ("clean", clean)):
    days = sorted({r["day"] for r in rs})
    if len(days) < 4:
        print(f"  {name}: too few sessions to split")
        continue
    mid = days[len(days) // 2]
    report(f"{name} early", [r for r in rs if r["day"] < mid])
    report(f"{name} late", [r for r in rs if r["day"] >= mid])

print("\n=== bootstrap: does the band beat baseline by luck? ===")
for name, rs in (("all closed", rows), ("clean", clean)):
    if len(rs) < 20:
        continue
    better = 0
    trials = 4000
    for _ in range(trials):
        samp = [random.choice(rs) for _ in rs]
        b_usd = sum(r["usd"] for r in samp)
        k_usd = sum(r["usd"] for r in samp if LO <= r["entry"] <= HI)
        if k_usd > b_usd:
            better += 1
    print(f"  {name:<12} band beats baseline in {better / trials * 100:>5.1f}% of {trials} resamples")

print("\n=== per-session, clean set ===")
for d in sorted({r['day'] for r in clean}):
    day_rows = [r for r in clean if r["day"] == d]
    b_usd, _, b_n = summarise(day_rows)
    k_usd, _, k_n = summarise(band(day_rows))
    print(f"  {d}  baseline ${b_usd:>8.2f} (n={b_n:<3}) -> band ${k_usd:>8.2f} (n={k_n:<3})  "
          f"{'better' if k_usd > b_usd else 'worse ' if k_usd < b_usd else 'same  '}")
