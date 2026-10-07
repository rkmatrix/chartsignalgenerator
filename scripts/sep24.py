"""Sep 24 trades, with peaks, holds, and why they closed."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    trades = book.get("trades") or []
    by_day: dict[str, list] = defaultdict(list)
    for t in trades:
        day = str(t.get("closed_at") or t.get("opened_at") or "")[:10]
        if day:
            by_day[day].append(t)

    print(f"{'day':<12} {'n':>3} {'P/L $':>9} {'win%':>6} {'green':>6} {'kept':>5}")
    for day in sorted(by_day)[-8:]:
        rows = [r for r in by_day[day] if r.get("status") == "closed" and r.get("pnl_dollars") is not None]
        dollars = sum(float(r["pnl_dollars"]) for r in rows)
        wins = sum(1 for r in rows if float(r["pnl_dollars"]) > 0)
        ever = kept = 0
        for r in rows:
            entry, peak = r.get("entry"), r.get("peak_mark")
            if entry and peak and float(peak) > float(entry):
                ever += 1
                if float(r["pnl_dollars"]) > 0:
                    kept += 1
        win = f"{wins / len(rows) * 100:.0f}%" if rows else "-"
        print(f"{day:<12} {len(rows):>3} {dollars:>+9.0f} {win:>6} {ever:>6} {kept:>5}")

    day = "2026-09-24"
    rows = by_day.get(day) or []
    print(f"\n=== {day}: {len(rows)} rows ===")
    for t in sorted(rows, key=lambda r: str(r.get("opened_at"))):
        entry = t.get("entry")
        peak = t.get("peak_mark")
        best = ""
        if entry and peak:
            best = f"peak {(float(peak) - float(entry)) / float(entry) * 100:+.0f}%"
        hold = ""
        if t.get("opened_at") and t.get("closed_at"):
            a = datetime.fromisoformat(t["opened_at"])
            b = datetime.fromisoformat(t["closed_at"])
            hold = f"{(b - a).total_seconds() / 60:.0f}m"
        marks = t.get("marks") or []
        gaps = []
        prev = None
        for m in marks:
            if isinstance(m, (list, tuple)) and m:
                ts = str(m[0])
                if prev:
                    try:
                        pa = datetime.strptime(prev, "%H:%M:%S")
                        pb = datetime.strptime(ts, "%H:%M:%S")
                        gaps.append((pb - pa).total_seconds())
                    except ValueError:
                        pass
                prev = ts
        gap = f"marks={len(marks)} med={sorted(gaps)[len(gaps)//2]:.0f}s" if gaps else f"marks={len(marks)}"
        wb = "wb-buy" if t.get("webull_buy_sent") else ""
        print(
            f"  {str(t.get('opened_at'))[11:16]} {str(t.get('ticker')):<6} "
            f"{str(t.get('direction')):<4} {str(t.get('verdict')):<5} "
            f"entry={entry} exit={t.get('exit')} pnl={t.get('pnl_pct')}% "
            f"${t.get('pnl_dollars')} {hold:>5} {best:>10} {t.get('reason')} "
            f"src={t.get('quote_source')} {gap} {wb}"
        )
        print(f"    {t.get('headline')}")
        print(f"    stack={'+'.join(t.get('strategies') or [])} window={t.get('window')} score={t.get('score')}")


if __name__ == "__main__":
    main()
