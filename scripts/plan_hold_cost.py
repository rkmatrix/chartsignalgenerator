"""Since the plan stop started waiting on the chart stop: did waiting pay?

For each live-feed trade opened on or after the change, find the first mark
whose bid was through -25%, and compare what selling there would have
realised with what the trade actually realised.
"""

from __future__ import annotations

import json
from pathlib import Path

H = Path(__file__).resolve().parents[1] / "data" / "history"
SINCE = "2026-09-28"


def main() -> None:
    trades = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))["trades"]
    gained = lost = 0.0
    for t in sorted(trades, key=lambda r: str(r.get("opened_at"))):
        if str(t.get("opened_at") or "") < SINCE or t.get("status") != "closed":
            continue
        if t.get("quote_source") != "uw":
            continue
        entry = float(t.get("entry") or 0)
        if not entry:
            continue
        first = None
        for m in t.get("marks") or []:
            if isinstance(m, list) and len(m) >= 3 and m[2]:
                if (float(m[2]) - entry) / entry * 100 <= -25:
                    first = m
                    break
        if not first:
            continue
        at_stop = (float(first[2]) - entry) * 100 * int(t.get("contracts") or 1)
        actual = float(t.get("pnl_dollars") or 0)
        diff = actual - at_stop
        if diff >= 0:
            gained += diff
        else:
            lost += diff
        print(f"{str(t['opened_at'])[:16]} {t['ticker']:<5} first -25% bid at {first[0]} "
              f"-> ${at_stop:+.0f}; actual ${actual:+.0f} ({t.get('reason')}) diff ${diff:+.0f}")
    print(f"\nwaiting helped ${gained:+.0f}, hurt ${lost:+.0f}")


if __name__ == "__main__":
    main()
