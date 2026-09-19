"""Fewer, better trades: which daily rule ends the most sessions green?

The desk currently trades every signal that clears its filters, all day, however
the day is going. Curriculum lesson 7.3 is the missing piece — a daily loss
limit, and its mirror, a profit lock that stops the desk handing back a green
day.

Each row is its own contract, so P&L does not depend on what else was held.
That makes "stop after N trades" or "stop once down $X" exactly replayable:
walk each session in open order and drop whatever the rule would have refused.

Scored on green days first and dollars second, because the stated goal is to
finish each session in profit rather than to maximise the total.

Usage: python scripts/backtest_daily_rules.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.open_session.ledger import risk_block  # noqa: E402

CLEAN_FROM = "2026-09-10"
book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)["trades"]

days: dict[str, list[dict]] = defaultdict(list)
for r in book:
    if r.get("status") != "closed" or r.get("pnl_dollars") is None or not r.get("entry"):
        continue
    # Only what the desk would take today: the premium band is already live.
    if risk_block({"entry": float(r["entry"]),
                   "plan_stop_pct": float(r.get("plan_stop_pct") or 25.0)}):
        continue
    days[str(r["opened_at"])[:10]].append({
        "at": str(r["opened_at"]),
        "usd": float(r["pnl_dollars"]),
        "ticker": r.get("ticker", ""),
    })
for d in days:
    days[d].sort(key=lambda x: x["at"])


def simulate(sessions: dict[str, list[dict]], max_n: int | None,
             loss_limit: float | None, profit_lock: float | None) -> tuple[float, int, int, int]:
    """Total dollars, green days, total days, trades taken."""
    total = 0.0
    green = 0
    taken = 0
    for _, rows in sessions.items():
        run = 0.0
        n = 0
        for row in rows:
            if max_n is not None and n >= max_n:
                break
            # Limits are checked before entering, on the running total, which is
            # what a desk can actually observe at decision time.
            if loss_limit is not None and run <= -loss_limit:
                break
            if profit_lock is not None and run >= profit_lock:
                break
            run += row["usd"]
            n += 1
            taken += 1
        total += run
        green += 1 if run > 0 else 0
    return total, green, len(sessions), taken


def show(label: str, sessions: dict, max_n=None, loss=None, lock=None) -> None:
    total, green, nd, taken = simulate(sessions, max_n, loss, lock)
    print(f"  {label:<34} ${total:>9.2f}   green {green}/{nd} = {green / nd * 100:>4.0f}%   "
          f"trades {taken:<4} (${total / max(1, taken):>+6.2f}/trade)")


for name, sel in (("ALL SESSIONS", days),
                  (f"LIVE FEED ONLY (from {CLEAN_FROM})",
                   {d: r for d, r in days.items() if d >= CLEAN_FROM})):
    if not sel:
        continue
    print(f"\n=== {name} — {len(sel)} sessions ===")
    show("baseline (band only, no daily rule)", sel)
    print()
    for n in (1, 2, 3, 4, 5):
        show(f"first {n} trade{'s' if n > 1 else ''} of the day", sel, max_n=n)
    print()
    for lim in (75.0, 100.0, 150.0, 200.0):
        show(f"stop the day once down ${lim:.0f}", sel, loss=lim)
    print()
    for lock in (25.0, 50.0, 75.0, 100.0):
        show(f"stop the day once up ${lock:.0f}", sel, lock=lock)
    print()
    for n, lim, lock in ((3, 100.0, 50.0), (3, 150.0, 75.0), (4, 100.0, 75.0),
                         (2, 100.0, 50.0), (3, 100.0, 75.0)):
        show(f"max {n}, stop -${lim:.0f}, lock +${lock:.0f}", sel,
             max_n=n, loss=lim, lock=lock)

print("\n=== session detail under 'max 3, stop -$100, lock +$75' (live feed) ===")
live = {d: r for d, r in days.items() if d >= CLEAN_FROM}
for d in sorted(live):
    rows = live[d]
    run, n, took = 0.0, 0, []
    for row in rows:
        if n >= 3 or run <= -100.0 or run >= 75.0:
            break
        run += row["usd"]
        n += 1
        took.append(f"{row['ticker']} {row['usd']:+.0f}")
    base = sum(x["usd"] for x in rows)
    print(f"  {d}  all-day ${base:>8.2f}  ->  ruled ${run:>8.2f}   "
          f"[{', '.join(took) if took else 'no trades'}]")
