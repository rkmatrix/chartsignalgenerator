"""What happened on a given session, trade by trade.

Shows the peak each trade reached before it closed, which is the number that
separates "the signal was wrong" from "the signal was right and we gave it
back". Those two failures have opposite fixes, so never read the P&L alone.

Usage: python scripts/analyze_today.py [YYYY-MM-DD]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)["trades"]

days = sorted({str(r.get("opened_at", ""))[:10] for r in book if r.get("opened_at")})
day = sys.argv[1] if len(sys.argv) > 1 else days[-1]

rows = [r for r in book if str(r.get("opened_at", ""))[:10] == day]
closed = [r for r in rows if r.get("status") == "closed" and r.get("pnl_dollars") is not None]
openish = [r for r in rows if r.get("status") != "closed"]

print(f"session {day}   opened {len(rows)}   closed {len(closed)}   still open {len(openish)}")
if not closed:
    print("nothing closed on this date")
    raise SystemExit

total = sum(float(r["pnl_dollars"]) for r in closed)
wins = [r for r in closed if float(r["pnl_dollars"]) > 0]
print(f"P/L ${total:+.2f}    winners {len(wins)}/{len(closed)} "
      f"({len(wins) / len(closed) * 100:.0f}%)\n")

hdr = (f"{'tkr':<6}{'dir':<5}{'vrd':<6}{'scr':<5}{'window':<12}"
       f"{'entry':>6}{'exit':>7}{'pnl%':>8}{'$':>9}{'peak%':>8}  reason")
print(hdr)
print("-" * len(hdr))

gave_back = []
for r in sorted(closed, key=lambda x: str(x.get("opened_at"))):
    entry = float(r.get("entry") or 0)
    peak = r.get("peak_mark")
    peak_pct = ((float(peak) - entry) / entry * 100.0) if peak and entry > 0 else 0.0
    pnl_pct = float(r.get("pnl_pct") or 0)
    if peak_pct >= 10 and pnl_pct <= 0:
        gave_back.append((r, peak_pct))
    print(f"{r.get('ticker',''):<6}{str(r.get('direction',''))[:4]:<5}"
          f"{str(r.get('verdict',''))[:4]:<6}{str(r.get('score','')):<5}"
          f"{str(r.get('window',''))[:11]:<12}{entry:>6.2f}"
          f"{float(r.get('exit') or 0):>7.2f}{pnl_pct:>+8.1f}"
          f"{float(r['pnl_dollars']):>+9.2f}{peak_pct:>+8.1f}  {r.get('reason','')}")

print(f"\nexit reasons: {Counter(r.get('reason') for r in closed).most_common()}")

print("\n=== went green, still closed red ===")
if not gave_back:
    print("  none - every loser was wrong from the start, not given back")
else:
    lost = sum(float(r["pnl_dollars"]) for r, _ in gave_back)
    peaks = ", ".join(f"{r.get('ticker')} +{p:.0f}%" for r, p in gave_back)
    print(f"  {len(gave_back)} trades, ${lost:+.2f}")
    print(f"  peaks reached: {peaks}")

print("\n=== was the direction right at all? ===")
never = [r for r in closed
         if not r.get("peak_mark")
         or (float(r["peak_mark"]) - float(r.get("entry") or 1)) / float(r.get("entry") or 1) * 100 < 3]
print(f"  never got 3% above entry: {len(never)}/{len(closed)}  "
      f"(${sum(float(r['pnl_dollars']) for r in never):+.2f})")
print("  -> these are signal-quality failures, not exit failures")
