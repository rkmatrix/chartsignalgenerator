"""Last sessions, with the conversion number the EOD now reports."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

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

    print(f"{len(trades)} rows, {len(by_day)} days\n")
    print(f"{'day':<12} {'n':>3} {'closed':>7} {'P/L $':>9} {'win%':>6} "
          f"{'went green':>11} {'kept':>6} {'conv':>7} {'live':>5}")
    running = 0.0
    for day in sorted(by_day):
        rows = by_day[day]
        closed = [r for r in rows if r.get("status") == "closed" and r.get("pnl_dollars") is not None]
        dollars = sum(float(r["pnl_dollars"]) for r in closed)
        running += dollars
        wins = sum(1 for r in closed if float(r["pnl_dollars"]) > 0)
        ever = kept = 0
        for r in closed:
            entry, peak = r.get("entry"), r.get("peak_mark")
            if entry is None or peak is None:
                continue
            try:
                if float(peak) > float(entry):
                    ever += 1
                    if float(r.get("pnl_dollars") or 0) > 0:
                        kept += 1
            except (TypeError, ValueError):
                pass
        live = sum(1 for r in closed if r.get("quote_source") == "uw")
        conv = f"{kept / ever * 100:.0f}%" if ever else "-"
        win = f"{wins / len(closed) * 100:.0f}%" if closed else "-"
        print(f"{day:<12} {len(rows):>3} {len(closed):>7} {dollars:>+9.0f} {win:>6} "
              f"{ever:>11} {kept:>6} {conv:>7} {live:>5}")
    print(f"\nrunning realised: ${running:,.0f}")

    today = datetime.now().strftime("%Y-%m-%d")
    rows = by_day.get(today) or []
    if not rows:
        days = sorted(by_day)
        today = days[-1]
        rows = by_day[today]
        print(f"\n(showing {today}, latest day on file)")
    print(f"\n=== {today} trade by trade ===")
    for t in sorted(rows, key=lambda r: str(r.get("opened_at"))):
        entry = t.get("entry")
        peak = t.get("peak_mark")
        best = ""
        if entry and peak:
            try:
                best = f"peak {(float(peak) - float(entry)) / float(entry) * 100:+.0f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        hold = ""
        if t.get("opened_at") and t.get("closed_at"):
            try:
                a = datetime.fromisoformat(t["opened_at"])
                b = datetime.fromisoformat(t["closed_at"])
                hold = f"{(b - a).total_seconds() / 60:.0f}m"
            except ValueError:
                pass
        print(f"  {str(t.get('opened_at'))[11:16]} {str(t.get('ticker')):<6} "
              f"{str(t.get('direction')):<4} {str(t.get('verdict')):<5} "
              f"entry={entry} exit={t.get('exit')} pnl={t.get('pnl_pct')}% "
              f"${t.get('pnl_dollars')} {hold:>5} {best:>10} {t.get('reason')} "
              f"src={t.get('quote_source')}")


if __name__ == "__main__":
    main()
