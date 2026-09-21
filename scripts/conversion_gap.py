"""Why does 64% direction accuracy come out as a 31% win rate?

On 2026-09-21 the engine's calls were right 64.3% of the time 30 and 60 minutes
out, measured on the underlying. The book wins 31.5% of trades. Spread alone
cannot account for a gap that size, so something between "the call was right"
and "the trade closed" is destroying correct predictions.

Every position now records peak_mark, trough_mark and a minute-by-minute marks
path, so the question is answerable directly rather than by inference: how many
trades were ever in profit, how much of their best moment they gave back, and
which exit reason was holding the knife.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"


def pct(a: float, b: float) -> float:
    return (b - a) / a * 100.0


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    rows = []
    for t in book.get("trades") or []:
        if t.get("status") != "closed" or t.get("quote_source") != "uw":
            continue
        entry, exit_px, pnl = t.get("entry"), t.get("exit"), t.get("pnl_pct")
        if entry is None or exit_px is None or pnl is None:
            continue
        try:
            entry, exit_px, pnl = float(entry), float(exit_px), float(pnl)
        except (TypeError, ValueError):
            continue
        if entry <= 0:
            continue
        hold = None
        if t.get("opened_at") and t.get("closed_at"):
            try:
                a = datetime.fromisoformat(t["opened_at"])
                b = datetime.fromisoformat(t["closed_at"])
                hold = (b - a).total_seconds() / 60.0
            except ValueError:
                pass
        rows.append({
            "tk": str(t.get("ticker") or "").upper(),
            "day": str(t.get("opened_at") or "")[:10],
            "entry": entry, "exit": exit_px, "pnl": pnl,
            "peak": t.get("peak_mark"), "trough": t.get("trough_mark"),
            "reason": str(t.get("reason") or "?"),
            "verdict": str(t.get("verdict") or "?"),
            "hold": hold,
            "marks": t.get("marks") or [],
        })

    print(f"{len(rows)} closed live-feed trades\n")

    # --- 1. How many were ever green, and how many stayed green? ---
    withpeak = [r for r in rows if r["peak"] is not None]
    print("=== the conversion gap ===")
    if withpeak:
        ever = [r for r in withpeak if float(r["peak"]) > r["entry"]]
        closed_green = [r for r in withpeak if r["pnl"] > 0]
        print(f"  trades with a recorded peak      : {len(withpeak)}")
        print(f"  ever showed a profit at the mark : {len(ever)} "
              f"({len(ever) / len(withpeak) * 100:.1f}%)")
        print(f"  actually closed green            : {len(closed_green)} "
              f"({len(closed_green) / len(withpeak) * 100:.1f}%)")
        roundtrip = [r for r in ever if r["pnl"] <= 0]
        print(f"  went green then closed red       : {len(roundtrip)} "
              f"({len(roundtrip) / max(1, len(ever)) * 100:.1f}% of the ones that worked)")
        if roundtrip:
            give = [pct(r["entry"], float(r["peak"])) for r in roundtrip]
            print(f"    their best unrealised gain was, on average, {mean(give):+.1f}% "
                  f"(median {median(give):+.1f}%)")
            print(f"    they closed at, on average, {mean(r['pnl'] for r in roundtrip):+.1f}%")
            lost = sum((float(r["peak"]) - r["exit"]) * 100.0 for r in roundtrip)
            print(f"    dollars handed back from peak to exit: ${lost:,.0f}")

    # --- 2. How much of the best moment do we keep? ---
    print("\n=== capture: how much of the peak does the exit keep? ===")
    keep = []
    for r in withpeak:
        best = pct(r["entry"], float(r["peak"]))
        if best <= 0:
            continue
        keep.append((r["pnl"] / best if best else 0.0, best, r))
    if keep:
        print(f"  {len(keep)} trades that were ever green")
        print(f"  mean peak gain offered   : {mean(k[1] for k in keep):+.1f}%")
        print(f"  mean realised            : {mean(k[2]['pnl'] for k in keep):+.1f}%")
        print(f"  mean share of peak kept  : {mean(k[0] for k in keep) * 100:.1f}%")

    # --- 3. Which exit reason does the damage? ---
    print("\n=== by exit reason ===")
    by = defaultdict(list)
    for r in rows:
        by[r["reason"]].append(r)
    print(f"  {'reason':<20} {'n':>4} {'mean %':>9} {'win%':>7} {'total $':>10} {'med hold':>9}")
    for reason, v in sorted(by.items(), key=lambda kv: sum(x["pnl"] for x in kv[1])):
        dollars = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 for r in v)
        wins = sum(1 for r in v if r["pnl"] > 0)
        holds = [r["hold"] for r in v if r["hold"] is not None]
        mh = f"{median(holds):.0f}m" if holds else "-"
        print(f"  {reason:<20} {len(v):>4} {mean(r['pnl'] for r in v):>+8.1f}% "
              f"{wins / len(v) * 100:>6.1f}% {dollars:>+10.0f} {mh:>9}")

    # --- 4. Did the trade ever have a chance? Trough before peak ---
    print("\n=== was the stop hit before the move had a chance? ===")
    both = [r for r in rows if r["peak"] is not None and r["trough"] is not None]
    if both:
        deep = [r for r in both if pct(r["entry"], float(r["trough"])) <= -25.0]
        print(f"  {len(both)} trades with both extremes recorded")
        print(f"  drew down 25% or more at some point: {len(deep)} "
              f"({len(deep) / len(both) * 100:.1f}%)")
        recovered = [r for r in deep if float(r["peak"]) > r["entry"]]
        print(f"    of those, later traded back above entry: {len(recovered)} "
              f"({len(recovered) / max(1, len(deep)) * 100:.1f}%)")
        print("    -> a 25% stop cuts these before they recover")
        print(f"  mean worst drawdown across all trades: "
              f"{mean(pct(r['entry'], float(r['trough'])) for r in both):+.1f}%")
        print(f"  mean best gain across all trades    : "
              f"{mean(pct(r['entry'], float(r['peak'])) for r in both):+.1f}%")

    # --- 5. Today specifically ---
    print("\n=== today ===")
    today = [r for r in rows if r["day"] == datetime.now().strftime("%Y-%m-%d")]
    if not today:
        days = sorted({r["day"] for r in rows})
        today = [r for r in rows if r["day"] == days[-1]]
        print(f"  (no rows for today's date; showing {days[-1]})")
    for r in today:
        best = pct(r["entry"], float(r["peak"])) if r["peak"] else None
        worst = pct(r["entry"], float(r["trough"])) if r["trough"] else None
        print(f"  {r['tk']:<6} {r['verdict']:<5} entry={r['entry']:.2f} exit={r['exit']:.2f} "
              f"{r['pnl']:+7.1f}%  best={best if best is None else f'{best:+.1f}%'} "
              f" worst={worst if worst is None else f'{worst:+.1f}%'}  {r['reason']}")


if __name__ == "__main__":
    main()
