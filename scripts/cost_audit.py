"""What does the round trip actually cost, and is any corner of it cheap?

The break-even bar (0.0797% of underlying) comes from a 5.79% round trip fitted
across all trades. But that is an average over very different contracts, and the
edge only has to beat the cost of the contracts actually traded. If SPY runs 1%
and a thin single-name runs 10%, the average is an artefact and the bar is
wrong -- too low for one and far too high for the other.

Measures the realised spread from live-NBBO rows, which record bid and mark, and
asks whether restricting to the cheapest corner lowers the bar enough for the
edges already measured to clear it:

  flow net_delta z>=+2        +0.027%
  tape and MACD both agree    +0.0044% / +0.0083% (early / late halves)
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pa.open_session.bandit import LEVERAGE, bucket_for  # noqa: E402

H = Path(__file__).resolve().parents[1] / "data" / "history"

FLOW_EDGE = 0.027
CONFLUENCE_EDGE = 0.0044  # the weaker of the two halves


def spreads() -> list[dict]:
    """Realised spread per quote, from rows priced on the live feed.

    mark is the mid and bid is what an exit actually fills at, so the full
    round trip is approximately 2*(mark-bid)/mark: half the spread paid on the
    way in, half on the way out.
    """
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    out = []
    for row in book.get("trades") or []:
        if str(row.get("quote_source") or "") != "uw":
            continue
        # Prefer the recorded path: many quotes per trade, not just the last.
        points = row.get("marks") or []
        samples = [(m, b) for _, m, b in points if m and b and float(m) > 0]
        if not samples:
            mark, bid = row.get("mark"), row.get("bid")
            if mark and bid and float(mark) > 0:
                samples = [(float(mark), float(bid))]
        if not samples:
            continue
        pcts = [2.0 * (m - b) / m * 100.0 for m, b in samples if m > b]
        if not pcts:
            continue
        out.append(
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "bucket": bucket_for(str(row.get("ticker") or "")),
                "entry": float(row.get("entry") or 0.0),
                "round_trip": median(pcts),
                "n_quotes": len(pcts),
            }
        )
    return out


def bar_for(round_trip: float) -> float:
    """Underlying move needed to break even at a given round-trip cost."""
    return round_trip / LEVERAGE


def main() -> None:
    rows = spreads()
    if not rows:
        print("no live-feed rows with recorded quotes yet")
        return

    allrt = [r["round_trip"] for r in rows]
    print(f"{len(rows)} live-feed trades, {sum(r['n_quotes'] for r in rows)} quotes\n")
    print("=== realised round trip, % of premium ===")
    s = sorted(allrt)
    for label, v in (
        ("cheapest 10%", s[len(s) // 10]),
        ("median", median(s)),
        ("mean (the 5.79% in use)", mean(s)),
        ("dearest 10%", s[-max(len(s) // 10, 1)]),
    ):
        print(f"  {label:<26} {v:6.2f}%   -> bar {bar_for(v):.4f}%")

    print("\n=== by ticker ===")
    by = defaultdict(list)
    for r in rows:
        by[r["ticker"]].append(r["round_trip"])
    print(f"  {'ticker':<8} {'n':>3} {'round trip':>11} {'bar':>9}  clears?")
    for tk, v in sorted(by.items(), key=lambda kv: median(kv[1])):
        rt = median(v)
        bar = bar_for(rt)
        flag = "FLOW" if FLOW_EDGE > bar else ("conf" if CONFLUENCE_EDGE > bar else "-")
        print(f"  {tk:<8} {len(v):>3} {rt:>10.2f}% {bar:>8.4f}%  {flag}")

    print("\n=== by premium band ===")
    bands = [(1.0, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 99.0)]
    print(f"  {'band':<14} {'n':>3} {'round trip':>11} {'bar':>9}")
    for lo, hi in bands:
        sel = [r["round_trip"] for r in rows if lo <= r["entry"] < hi]
        if not sel:
            continue
        rt = median(sel)
        print(f"  ${lo:.2f}-${hi:<7.2f} {len(sel):>3} {rt:>10.2f}% {bar_for(rt):>8.4f}%")

    print("\n=== the question that matters ===")
    cheap = [r for r in rows if r["round_trip"] <= s[len(s) // 4]]
    if cheap:
        rt = median(r["round_trip"] for r in cheap)
        bar = bar_for(rt)
        names = sorted({r["ticker"] for r in cheap})
        print(f"  cheapest quartile: {len(cheap)} trades, round trip {rt:.2f}%, bar {bar:.4f}%")
        print(f"  names: {', '.join(names)}")
        print()
        for label, edge in (("flow net_delta z>=+2", FLOW_EDGE), ("tape+MACD agree", CONFLUENCE_EDGE)):
            verdict = "CLEARS" if edge > bar else f"short by {bar - edge:.4f}%"
            print(f"  {label:<24} {edge:+.4f}%  vs bar {bar:.4f}%  -> {verdict}")


if __name__ == "__main__":
    main()
