"""How far under our fill was the first bid, and what did that do to the trade?

The buy fills at the ask. A trade whose first bid is already 25% under the
fill is born at the plan stop.
"""

from __future__ import annotations

import json
from pathlib import Path

H = Path(__file__).resolve().parents[1] / "data" / "history"


def main() -> None:
    trades = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))["trades"]
    rows = []
    for t in trades:
        if t.get("status") != "closed" or t.get("quote_source") != "uw":
            continue
        marks = t.get("marks") or []
        entry = float(t.get("entry") or 0)
        first_bid = None
        for m in marks[:3]:
            if isinstance(m, list) and len(m) >= 3 and m[2]:
                first_bid = float(m[2])
                break
        if not entry or first_bid is None:
            continue
        gap = (entry - first_bid) / entry * 100.0
        rows.append((gap, float(t.get("pnl_dollars") or 0), float(t.get("pnl_pct") or 0),
                     str(t.get("opened_at"))[:16], t.get("ticker"), t.get("reason")))

    print(f"{len(rows)} live-feed closes with a first bid")
    bands = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 101)]
    for lo, hi in bands:
        sub = [r for r in rows if lo <= r[0] < hi]
        if not sub:
            continue
        wins = sum(1 for r in sub if r[1] > 0)
        print(f"  gap {lo:>2}-{hi:<3}% n={len(sub):>3} win {wins / len(sub) * 100:5.1f}% "
              f"total ${sum(r[1] for r in sub):+7.0f} avg ${sum(r[1] for r in sub) / len(sub):+6.1f}")
    for cut in (8, 10, 12, 15):
        kept = [r for r in rows if r[0] < cut]
        cut_rows = [r for r in rows if r[0] >= cut]
        print(f"refuse at >= {cut}%: refuse {len(cut_rows)} (${sum(r[1] for r in cut_rows):+.0f}), "
              f"keep {len(kept)} (${sum(r[1] for r in kept):+.0f})")
    print("\nworst gaps:")
    for r in sorted(rows, reverse=True)[:14]:
        print(f"  {r[3]} {r[4]:<5} gap {r[0]:5.1f}% ${r[1]:+6.0f} {r[2]:+6.1f}% {r[5]}")


if __name__ == "__main__":
    main()
