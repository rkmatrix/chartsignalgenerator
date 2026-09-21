"""Dump the recorded quote path for recent trades.

The conversion-gap numbers lean entirely on peak_mark, and peak_mark is only as
good as the quotes that produced it. MSFT on 2026-09-21 supposedly ran +35.9%
and closed -2.6% inside 102 seconds, which is either a real gap or a bad tick
polluting the peak. Those two call for opposite fixes, so look at the ticks.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"

day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))

rows = [t for t in book.get("trades") or [] if str(t.get("opened_at") or "")[:10] == day]
print(f"{len(rows)} trades on {day}\n")

for t in sorted(rows, key=lambda r: str(r.get("opened_at"))):
    entry = t.get("entry")
    print(f"=== {t.get('ticker')} {t.get('direction')} {t.get('verdict')} ===")
    print(f"  opened {str(t.get('opened_at'))[11:19]}  closed {str(t.get('closed_at'))[11:19]}")
    print(f"  entry={entry} exit={t.get('exit')} pnl={t.get('pnl_pct')}% reason={t.get('reason')}")
    print(f"  peak_mark={t.get('peak_mark')} trough_mark={t.get('trough_mark')} "
          f"quote_source={t.get('quote_source')} quote_age={t.get('quote_age')}")
    marks = t.get("marks") or []
    print(f"  recorded marks: {len(marks)}")
    for m in marks:
        if isinstance(m, dict):
            ts = str(m.get("ts") or m.get("at") or "")[11:19]
            px = m.get("mark") if m.get("mark") is not None else m.get("px")
            bid = m.get("bid")
            pc = "" if (px is None or not entry) else f"  ({(float(px) - float(entry)) / float(entry) * 100:+.1f}%)"
            print(f"    {ts}  mark={px}  bid={bid}{pc}")
        else:
            print(f"    {m}")
    print(f"  headline: {t.get('headline')}")
    print()
