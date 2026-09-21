"""Is MAX_PREMIUM secretly a time-of-day filter on expensive underlyings?

META printed 14 call signals today, the first at 10:03 with price only +3.09%
off the open. Exactly one was booked: 14:34, at +8.36%, near the high, for
-27.7%. Crowding cannot explain it -- every position was closed by 10:27.

The suspicion is arithmetic. An at-the-money 0DTE premium is mostly time value,
which decays with the square root of time remaining. A contract costing $6.70 at
10:00 is worth about $3.25 by 14:30 with nothing else changing. A hard $3.50
ceiling therefore does not refuse expensive contracts, it DELAYS them: a costly
underlying becomes affordable only once the day is nearly over, which is both
the worst moment to buy a trend and the point at which there is no time left for
the thesis to work.

If true, entries on high-priced names should cluster late in the session while
cheap ones spread across it. That is a pattern in data already on disk, not a
model, so it can be checked directly.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

H = ROOT / "data" / "history"


def spot_for(ticker: str) -> float | None:
    for name in (f"{ticker}_1m_hist.json", f"{ticker}_1m_today.json"):
        p = H / name
        if not p.exists():
            continue
        try:
            bars = json.loads(p.read_text(encoding="utf-8"))["bars"]
            if bars:
                return float(bars[-1]["close"])
        except Exception:
            continue
    return None


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    rows = []
    for t in book.get("trades") or []:
        opened = str(t.get("opened_at") or "")
        tk = str(t.get("ticker") or "").upper()
        if not opened or not tk or t.get("entry") is None:
            continue
        try:
            ts = datetime.fromisoformat(opened)
        except ValueError:
            continue
        mins = (ts.hour - 9) * 60 + ts.minute - 30
        if not (0 <= mins <= 390):
            continue
        spot = spot_for(tk)
        if not spot:
            continue
        rows.append({
            "tk": tk, "mins": mins, "entry": float(t["entry"]), "spot": spot,
            "pnl": t.get("pnl_pct"),
        })

    if not rows:
        print("no usable rows")
        return

    print(f"{len(rows)} booked entries with a known underlying price\n")

    # Split names by how expensive the underlying is: a 0DTE ATM premium scales
    # roughly with the share price, so dear stocks make dear contracts.
    by_tk = defaultdict(list)
    for r in rows:
        by_tk[r["tk"]].append(r)
    tk_spot = {tk: v[0]["spot"] for tk, v in by_tk.items()}
    cutoff = median(tk_spot.values())

    print(f"median underlying across traded names: ${cutoff:.0f}\n")
    print("=== when does the desk enter, by how expensive the underlying is? ===")
    print(f"  {'group':<28} {'n':>4} {'median entry time':>18} {'mean premium':>13}")
    for label, pred in (
        ("dear names (spot >= median)", lambda r: r["spot"] >= cutoff),
        ("cheap names (spot < median)", lambda r: r["spot"] < cutoff),
    ):
        sel = [r for r in rows if pred(r)]
        if not sel:
            continue
        med = median(r["mins"] for r in sel)
        hh = 9 + int((30 + med) // 60)
        mm = int((30 + med) % 60)
        print(f"  {label:<28} {len(sel):>4} {f'{hh:02d}:{mm:02d} ET':>18} "
              f"{mean(r['entry'] for r in sel):>12.2f}")

    print("\n=== per ticker, sorted by share price ===")
    print(f"  {'ticker':<7} {'spot':>8} {'n':>4} {'median entry':>13} {'mean prem':>10}")
    for tk, v in sorted(by_tk.items(), key=lambda kv: -kv[1][0]["spot"]):
        if len(v) < 2:
            continue
        med = median(r["mins"] for r in v)
        hh = 9 + int((30 + med) // 60)
        mm = int((30 + med) % 60)
        print(f"  {tk:<7} {v[0]['spot']:>8.0f} {len(v):>4} {f'{hh:02d}:{mm:02d}':>13} "
              f"{mean(r['entry'] for r in v):>10.2f}")

    print("\n=== does a late entry do worse? ===")
    graded = [r for r in rows if r["pnl"] is not None]
    for label, pred in (
        ("entered before 12:00", lambda r: r["mins"] < 150),
        ("entered after 13:30", lambda r: r["mins"] >= 240),
    ):
        sel = [r for r in graded if pred(r)]
        if len(sel) < 5:
            continue
        wins = sum(1 for r in sel if float(r["pnl"]) > 0)
        print(f"  {label:<24} n={len(sel):<4} mean={mean(float(r['pnl']) for r in sel):+7.2f}% "
              f"win={wins / len(sel) * 100:5.1f}%")

    print("\n=== the mechanism, priced out ===")
    print("  An ATM 0DTE premium is mostly time value, which decays with sqrt(time).")
    print("  Relative to a 14:30 entry, the same contract earlier in the day costs:")
    for hh, mm in ((10, 0), (11, 30), (13, 0), (14, 30)):
        left = (16 - hh) * 60 - mm
        ref = (16 - 14) * 60 - 30
        mult = (left / ref) ** 0.5
        print(f"    {hh:02d}:{mm:02d}  x{mult:.2f}  -> a $3.25 contract costs ${3.25 * mult:.2f}"
              f"{'   REFUSED by the $3.50 cap' if 3.25 * mult > 3.50 else ''}")


if __name__ == "__main__":
    main()
