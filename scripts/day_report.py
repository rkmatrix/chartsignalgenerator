"""One session from the book: every trade, why it closed, and its path."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "data" / "history" / "signal_book.json"


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    trades = json.loads(BOOK.read_text(encoding="utf-8"))["trades"]

    totals: dict[str, float] = defaultdict(float)
    for t in trades:
        if t.get("status") == "closed" and t.get("pnl_dollars") is not None:
            totals[str(t.get("opened_at") or "")[:10]] += float(t["pnl_dollars"])
    print("recent days:", {d: round(v) for d, v in sorted(totals.items())[-8:]})

    rows = [t for t in trades if str(t.get("opened_at") or "").startswith(day)]
    rows.sort(key=lambda t: str(t.get("opened_at")))
    print(f"\n{day}: {len(rows)} rows")
    reasons: Counter = Counter()
    by_reason: dict[str, float] = defaultdict(float)
    by_ticker: dict[str, float] = defaultdict(float)
    by_strat: dict[str, float] = defaultdict(float)
    for t in rows:
        pnl = float(t.get("pnl_dollars") or 0)
        reasons[t.get("reason")] += 1
        by_reason[str(t.get("reason"))] += pnl
        by_ticker[str(t.get("ticker"))] += pnl
        by_strat["+".join(t.get("strategies") or [])] += pnl
        opened = str(t.get("opened_at"))[11:19]
        closed = str(t.get("closed_at") or "")[11:19]
        marks = t.get("marks") or []
        peak = t.get("peak_mark")
        entry = float(t.get("entry") or 0) or None
        peak_pct = None if not (peak and entry) else (float(peak) - entry) / entry * 100
        peak_bid = t.get("peak_bid")
        pb_pct = None if not (peak_bid and entry) else (float(peak_bid) - entry) / entry * 100
        print(
            f"{opened}-{closed} {t.get('ticker'):<5} {t.get('direction'):<4} "
            f"K{t.get('strike')} {t.get('verdict'):<5} q={t.get('quote_source')} "
            f"in {t.get('entry')} out {t.get('exit')} "
            f"{(t.get('pnl_pct') or 0):+6.1f}% ${pnl:+7.0f} "
            f"peak {'' if peak_pct is None else f'{peak_pct:+.0f}%'} "
            f"pkbid {'' if pb_pct is None else f'{pb_pct:+.0f}%'} "
            f"spot {t.get('spot')} stop {t.get('stop')} n={len(marks)} "
            f"{t.get('reason')} | {t.get('strategies')} | {t.get('headline') or ''}"
        )
    print("\nby reason:", {k: (reasons[k], round(v)) for k, v in by_reason.items()})
    print("by ticker:", {k: round(v) for k, v in sorted(by_ticker.items(), key=lambda kv: kv[1])})
    print("by strategy:", {k: round(v) for k, v in by_strat.items()})


if __name__ == "__main__":
    main()
