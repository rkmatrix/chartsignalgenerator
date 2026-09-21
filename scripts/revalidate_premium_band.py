"""Re-test the $1.00 premium floor using only real-feed trades.

Today the desk quoted ATM 0DTE contracts at $0.50 (SPY), $0.18 (IWM), $0.60
(NVDA), $0.87 (MSFT), $0.92 (QQQ) and $0.97 (NFLX). Every one was refused for
being under MIN_PREMIUM, whose stated justification is that on cheap contracts
"the spread is wider than the edge". Those are also the contracts that turn a
1% move in the underlying into a multi-hundred-percent move in the option, which
is exactly the kind of day the desk just sat out.

Two reasons to distrust the floor. It was fitted on a book dominated by the
15-minute-delayed Yahoo chain -- the same feed that manufactured a +28% edge
that did not exist -- and the later cost audit found SPY and QQQ to have the
TIGHTEST round trips of anything traded, which is the opposite of the premise.

So measure the spread directly, per premium band, on rows priced by Unusual
Whales, where bid and mark were both recorded at entry.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"

BANDS = [
    (0.00, 0.50, "under $0.50"),
    (0.50, 1.00, "$0.50 - $1.00"),
    (1.00, 2.00, "$1.00 - $2.00"),
    (2.00, 3.50, "$2.00 - $3.50"),
    (3.50, 1e9, "over $3.50"),
]


def band_of(x: float) -> str:
    for lo, hi, label in BANDS:
        if lo <= x < hi:
            return label
    return "?"


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    trades = book.get("trades") or []

    live = [t for t in trades if t.get("quote_source") == "uw"]
    delayed = [t for t in trades if t.get("quote_source") != "uw"]
    print(f"{len(trades)} trades total: {len(live)} priced live, {len(delayed)} delayed\n")

    # --- 1. What does a round trip actually cost, by premium band? ---
    print("=== measured round-trip spread cost, live-feed rows only ===")
    print("  (entry lifts the ask, exit hits the bid; cost = (mark-bid)/mark, doubled)")
    print(f"  {'band':<16} {'n':>4} {'median cost':>12} {'mean cost':>11}")
    costs = defaultdict(list)
    for t in live:
        entry, bid, mark = t.get("entry"), t.get("bid"), t.get("mark")
        if not entry or not bid or not mark:
            continue
        try:
            bid, mark = float(bid), float(mark)
        except (TypeError, ValueError):
            continue
        if mark <= 0 or bid <= 0 or bid > mark:
            continue
        costs[band_of(float(entry))].append((mark - bid) / mark * 2.0 * 100.0)
    for _lo, _hi, label in BANDS:
        v = costs.get(label) or []
        if not v:
            print(f"  {label:<16} {0:>4}  {'no rows':>12}")
            continue
        print(f"  {label:<16} {len(v):>4} {median(v):>11.1f}% {mean(v):>10.1f}%")

    # --- 2. Did cheap contracts actually lose, once priced honestly? ---
    print("\n=== realized P&L by band ===")
    for source, rows in (("LIVE feed", live), ("delayed feed", delayed)):
        print(f"\n  -- {source} --")
        print(f"  {'band':<16} {'n':>4} {'mean P&L':>10} {'median':>9} {'win%':>7} {'total $':>10}")
        by = defaultdict(list)
        for t in rows:
            entry, pnl = t.get("entry"), t.get("pnl_pct")
            if entry is None or pnl is None:
                continue
            try:
                by[band_of(float(entry))].append((float(pnl), float(entry)))
            except (TypeError, ValueError):
                continue
        for _lo, _hi, label in BANDS:
            v = by.get(label) or []
            if not v:
                continue
            pnls = [p for p, _ in v]
            dollars = sum(p / 100.0 * e * 100.0 for p, e in v)
            wins = sum(1 for p in pnls if p > 0)
            print(f"  {label:<16} {len(v):>4} {mean(pnls):>9.1f}% {median(pnls):>8.1f}% "
                  f"{wins / len(v) * 100:>6.1f}% {dollars:>+10.0f}")

    # --- 3. What is the floor costing us in signals? ---
    print("\n=== what the floor refuses right now ===")
    cache = json.loads((H / "contracts_cache.json").read_text(encoding="utf-8"))
    refused = [(k, v) for k, v in cache.items()
               if isinstance(v, dict) and v.get("entry") is not None
               and float(v["entry"]) < 1.00]
    print(f"  {len(refused)} of {len(cache)} quoted contracts are under the $1.00 floor:")
    for k, v in sorted(refused, key=lambda kv: float(kv[1]["entry"])):
        entry, spot, strike = float(v["entry"]), v.get("spot"), v.get("strike")
        print(f"    {k:<28} ${entry:.2f}  spot={spot} strike={strike}")

    # --- 4. Leverage: why cheap 0DTE is the point, not the problem ---
    print("\n=== leverage on a cheap ATM 0DTE contract ===")
    print("  A $0.50 ATM SPY call is almost pure time value. If SPY runs 1%,")
    print("  the strike goes deep in the money and the contract prices near intrinsic:")
    spot, strike, prem = 772.73, 773.0, 0.50
    for mv in (0.25, 0.50, 1.00, 1.50):
        intrinsic = max(0.0, spot * (1 + mv / 100.0) - strike)
        print(f"    SPY +{mv:.2f}%  ->  intrinsic ${intrinsic:5.2f}  "
              f"= {(intrinsic - prem) / prem * 100:+7.0f}% on a ${prem:.2f} entry")
    print("\n  Same 1% move on the $3.25 META contract the desk DID take:")
    spot, strike, prem = 747.86, 747.5, 3.25
    for mv in (0.25, 0.50, 1.00, 1.50):
        intrinsic = max(0.0, spot * (1 + mv / 100.0) - strike)
        print(f"    META +{mv:.2f}%  ->  intrinsic ${intrinsic:5.2f}  "
              f"= {(intrinsic - prem) / prem * 100:+7.0f}% on a ${prem:.2f} entry")


if __name__ == "__main__":
    main()
