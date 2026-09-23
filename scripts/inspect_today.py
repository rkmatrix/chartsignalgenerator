"""Today's quote paths, and whether the exit watcher was looking."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"

book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
rows = [t for t in book.get("trades") or [] if str(t.get("opened_at") or "")[:10] == "2026-09-22"]

for t in sorted(rows, key=lambda r: str(r.get("opened_at"))):
    entry = float(t["entry"])
    print(f"=== {t.get('ticker')} {t.get('direction')} {t.get('verdict')} "
          f"score={t.get('score')} {t.get('reason')} ===")
    print(f"  {str(t.get('opened_at'))[11:19]} -> {str(t.get('closed_at'))[11:19]}  "
          f"entry={entry} exit={t.get('exit')} pnl={t.get('pnl_pct')}% "
          f"peak={t.get('peak_mark')} trough={t.get('trough_mark')}")
    print(f"  stack={'+'.join(t.get('strategies') or [])} window via headline:")
    print(f"  {t.get('headline')}")
    marks = t.get("marks") or []
    print(f"  {len(marks)} marks")
    gaps = []
    prev = None
    for m in marks:
        if not (isinstance(m, (list, tuple)) and len(m) >= 2):
            print(f"    {m}")
            continue
        ts, mark = str(m[0]), float(m[1])
        bid = float(m[2]) if len(m) > 2 and m[2] is not None else None
        sell = bid if bid is not None else mark
        pc = (sell - entry) / entry * 100.0
        gap = ""
        if prev is not None:
            try:
                a = datetime.strptime(prev, "%H:%M:%S")
                b = datetime.strptime(ts, "%H:%M:%S")
                sec = (b - a).total_seconds()
                gaps.append(sec)
                gap = f"  +{sec:.0f}s"
            except ValueError:
                pass
        prev = ts
        print(f"    {ts}  mark={mark:.2f} bid={bid}  sellable {pc:+.1f}%{gap}")
    if gaps:
        gaps.sort()
        print(f"  spacing median {gaps[len(gaps)//2]:.0f}s  max {max(gaps):.0f}s  n={len(gaps)}")
    print()
