"""Replay the book under the old gates and the new ones.

Two changes need checking together, because they pull in opposite directions.
Lowering the floor to $0.50 lets more trades in, and several of them are from
the cheap end that used to be refused. Sizing to a fixed risk budget then
multiplies every position, so a bad trade costs more dollars than it used to.

The honest limits of this test, stated up front: the book only contains trades
the desk actually took, so it cannot show what the 12 SPY signals it never
booked would have done. It can only re-price decisions already made. Rows below
$1.00 exist only because they predate the floor. And percentage outcomes are
held fixed under sizing, which assumes a bigger order fills at the same price --
true enough for SPY and QQQ, optimistic for the thin names.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.ledger import contracts_for  # noqa: E402

OLD_MIN, OLD_MAX = 1.00, 3.50
NEW_MIN = 0.50


def main() -> None:
    book = json.loads((ROOT / "data" / "history" / "signal_book.json").read_text(encoding="utf-8"))
    rows = []
    for t in book.get("trades") or []:
        entry, pnl = t.get("entry"), t.get("pnl_pct")
        if entry is None or pnl is None or t.get("status") != "closed":
            continue
        try:
            entry, pnl = float(entry), float(pnl)
        except (TypeError, ValueError):
            continue
        if entry <= 0:
            continue
        rows.append({
            "tk": str(t.get("ticker") or "").upper(),
            "entry": entry, "pnl": pnl,
            "live": t.get("quote_source") == "uw",
            "day": str(t.get("opened_at") or "")[:10],
            "stop": float(t.get("plan_stop_pct") or 25.0),
        })

    live = [r for r in rows if r["live"]]
    print(f"{len(rows)} closed trades, {len(live)} priced on the live feed\n")

    def policy(rows_in, lo, hi, sized):
        kept, dollars = [], 0.0
        for r in rows_in:
            if r["entry"] < lo or (hi is not None and r["entry"] > hi):
                continue
            n = contracts_for(r["entry"], r["stop"]) if sized else 1
            if n <= 0:
                continue
            kept.append(r)
            dollars += r["pnl"] / 100.0 * r["entry"] * 100.0 * n
        return kept, dollars

    print("=== live-feed trades only (the only rows that can settle anything) ===")
    print(f"  {'policy':<42} {'trades':>7} {'total $':>10} {'win%':>7} {'mean %':>8}")
    variants = [
        ("old: $1.00-$3.50 band, 1 contract", OLD_MIN, OLD_MAX, False),
        ("floor $0.50 only, 1 contract", NEW_MIN, None, False),
        ("NEW: floor $0.50, sized to risk", NEW_MIN, None, True),
        ("(control) no gate at all, 1 contract", 0.0, None, False),
    ]
    results = {}
    for label, lo, hi, sized in variants:
        kept, dollars = policy(live, lo, hi, sized)
        if not kept:
            continue
        wins = sum(1 for r in kept if r["pnl"] > 0)
        results[label] = (kept, dollars)
        print(f"  {label:<42} {len(kept):>7} {dollars:>+10.0f} "
              f"{wins / len(kept) * 100:>6.1f}% {mean(r['pnl'] for r in kept):>+7.1f}%")

    print("\n=== what the floor change alone lets back in ===")
    added = [r for r in live if NEW_MIN <= r["entry"] < OLD_MIN]
    if added:
        d = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 for r in added)
        wins = sum(1 for r in added if r["pnl"] > 0)
        print(f"  {len(added)} trades in $0.50-$1.00: {d:+.0f} at 1 contract, "
              f"{wins / len(added) * 100:.0f}% win, mean {mean(r['pnl'] for r in added):+.1f}%")
        for r in sorted(added, key=lambda x: x["pnl"]):
            print(f"    {r['day']}  {r['tk']:<6} ${r['entry']:.2f}  {r['pnl']:+7.1f}%")
    dropped = [r for r in live if r["entry"] > OLD_MAX and contracts_for(r["entry"], r["stop"]) > 0]
    if dropped:
        d = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 * contracts_for(r["entry"], r["stop"])
                for r in dropped)
        print(f"\n  {len(dropped)} trades over $3.50 now allowed and sized: {d:+.0f}")
        for r in sorted(dropped, key=lambda x: x["pnl"]):
            print(f"    {r['day']}  {r['tk']:<6} ${r['entry']:.2f} "
                  f"x{contracts_for(r['entry'], r['stop'])}  {r['pnl']:+7.1f}%")

    print("\n=== day by day, live feed, old vs new ===")
    old_kept, _ = policy(live, OLD_MIN, OLD_MAX, False)
    new_kept, _ = policy(live, NEW_MIN, None, True)
    days = sorted({r["day"] for r in live})
    print(f"  {'day':<12} {'old $':>9} {'new $':>9} {'old n':>6} {'new n':>6}")
    for day in days:
        o = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 for r in old_kept if r["day"] == day)
        n = sum(r["pnl"] / 100.0 * r["entry"] * 100.0 * contracts_for(r["entry"], r["stop"])
                for r in new_kept if r["day"] == day)
        on = sum(1 for r in old_kept if r["day"] == day)
        nn = sum(1 for r in new_kept if r["day"] == day)
        print(f"  {day:<12} {o:>+9.0f} {n:>+9.0f} {on:>6} {nn:>6}")

    print("\n=== how big do positions get? ===")
    by_tk = defaultdict(list)
    for r in live:
        n = contracts_for(r["entry"], r["stop"])
        if n > 0 and r["entry"] >= NEW_MIN:
            by_tk[r["tk"]].append((n, r["entry"]))
    print(f"  {'ticker':<8} {'median lots':>12} {'median cost':>12}")
    for tk, v in sorted(by_tk.items(), key=lambda kv: -mean(x[0] for x in kv[1])):
        lots = mean(x[0] for x in v)
        cost = mean(x[0] * x[1] * 100 for x in v)
        print(f"  {tk:<8} {lots:>12.1f} {cost:>11.0f}")


if __name__ == "__main__":
    main()
