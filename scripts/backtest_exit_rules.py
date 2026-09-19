"""Where should we exit? Replay the same trades under different rules.

Today's SPY sold at +$33 while SPY was still making new lows: the ratchet
watches the premium give back half a run and cannot tell a dying trade from a
breathing one. Every row already carries the structural level the thesis was
built on (`stop`/`trigger`), so exiting on that instead is testable.

Rules, all priced at intrinsic at the moment of exit:
  booked     what the desk actually realised
  thesis     hold until the underlying crosses back through the trade's level
  eod        hold to the close, the no-exit baseline
  trailN     give the underlying N times its entry distance-to-level, then quit

Intrinsic ignores time value, so every simulated rule here is understated while
`booked` is real money. Any rule that still wins is winning against a handicap.

Usage: python scripts/backtest_exit_rules.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ET = ZoneInfo("America/New_York")
hist = Path(r"C:\Projects\trading\PA\data\history")

tape: dict[str, dict[str, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(dict))
for path in list(hist.glob("*_1m_hist.json")) + list(hist.glob("*_1m_today.json")):
    tk = path.name.split("_")[0].upper()
    try:
        bars = json.loads(path.read_text(encoding="utf-8"))["bars"]
    except Exception:
        continue
    for b in bars:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30) or ts.hour >= 16:
            continue
        tape[tk][ts.date().isoformat()][ts.strftime("%H:%M")] = float(b["close"])

book = json.loads((hist / "signal_book.json").read_text(encoding="utf-8"))["trades"]

trades = []
for r in book:
    if r.get("status") != "closed" or r.get("pnl_dollars") is None:
        continue
    if not all(r.get(k) for k in ("strike", "entry", "opened_at")):
        continue
    tk = str(r.get("ticker") or "").upper()
    day = str(r["opened_at"])[:10]
    if str(r.get("expiry") or "")[:10] != day or day not in tape.get(tk, {}):
        continue
    level = r.get("stop") or r.get("trigger")
    if not level:
        continue
    opened = datetime.fromisoformat(str(r["opened_at"])).astimezone(ET).strftime("%H:%M")
    series = sorted((t, c) for t, c in tape[tk][day].items() if t >= opened)
    if len(series) < 5:
        continue
    trades.append({
        "tk": tk, "day": day, "entry": float(r["entry"]), "k": float(r["strike"]),
        "put": str(r.get("direction") or "").lower().startswith("p"),
        "level": float(level), "booked": float(r["pnl_dollars"]),
        "series": series,
    })


def intrinsic(t: dict, spot: float) -> float:
    return max(0.0, (t["k"] - spot) if t["put"] else (spot - t["k"]))


def pnl(t: dict, spot: float) -> float:
    return (intrinsic(t, spot) - t["entry"]) * 100.0


def run(t: dict, mode: str, mult: float = 1.0) -> float:
    spot0 = t["series"][0][1]
    room = abs(spot0 - t["level"]) * mult
    for _, spot in t["series"]:
        if mode == "thesis":
            # Level breached against us: the reason for the trade is gone.
            if (t["put"] and spot > t["level"]) or (not t["put"] and spot < t["level"]):
                return pnl(t, spot)
        elif mode == "trail":
            edge = (t["level"] + room) if t["put"] else (t["level"] - room)
            if (t["put"] and spot > edge) or (not t["put"] and spot < edge):
                return pnl(t, spot)
    return pnl(t, t["series"][-1][1])


print(f"replaying {len(trades)} closed 0DTE trades\n")


def show(label: str, vals: list[float]) -> None:
    wins = sum(1 for v in vals if v > 0)
    print(f"  {label:<14} total ${sum(vals):>9.2f}   per trade ${sum(vals) / len(vals):>+7.2f}   "
          f"green {wins}/{len(vals)} = {wins / len(vals) * 100:>4.1f}%")


show("booked", [t["booked"] for t in trades])
show("thesis", [run(t, "thesis") for t in trades])
for m in (1.5, 2.0, 3.0):
    show(f"trail x{m}", [run(t, "trail", m) for t in trades])
show("eod", [run(t, "eod") for t in trades])

print("\n=== booked vs thesis-exit, by session ===")
for day in sorted({t["day"] for t in trades}):
    grp = [t for t in trades if t["day"] == day]
    b = sum(t["booked"] for t in grp)
    th = sum(run(t, "thesis") for t in grp)
    print(f"  {day}  n={len(grp):<3} booked ${b:>9.2f}   thesis ${th:>9.2f}   "
          f"{'better' if th > b else 'worse'}")
