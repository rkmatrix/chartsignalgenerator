"""Measure the signals the live desk would actually have alerted on.

backtest_hunt.py throttles with a fixed bar cooldown, which is the Pine chart's
rule, not the desk's. The desk keys a position as {ticker}-{direction}-{day} and
refuses the opposite side once one is open, so in practice it alerts at most
once per ticker per day. That is a very different population from "every signal
the logic emits", and it is the one that reaches Telegram.

Usage: python scripts/backtest_desk_rule.py [TICKER] [hold_bars] [take_cut]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, r"C:\Projects\trading\PA\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for  # noqa: E402
from pa.open_session.playbook import apply_playbook  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
HOLD = int(sys.argv[2]) if len(sys.argv) > 2 else 15
TAKE_CUT = int(sys.argv[3]) if len(sys.argv) > 3 else 75

hist_dir = Path(r"C:\Projects\trading\PA\data\history")
path = hist_dir / f"{TICKER}_1m_hist.json"
if not path.exists():
    path = hist_dir / f"{TICKER}_1m_today.json"
payload = json.loads(path.read_text(encoding="utf-8"))
first_prior = payload.get("prior_close")

bars = [
    Bar(
        ticker=TICKER,
        ts=datetime.fromisoformat(b["ts"]),
        open=float(b["open"]),
        high=float(b["high"]),
        low=float(b["low"]),
        close=float(b["close"]),
        volume=float(b["volume"]),
        timeframe=Timeframe.M1,
    )
    for b in payload["bars"]
]

by_day: dict[str, list[Bar]] = {}
for bar in bars:
    by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)

full = [
    d
    for d in sorted(by_day)
    if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300
]
days = full
print(f"{TICKER}: {len(days)} usable sessions, hold={HOLD} bars, take_cut={TAKE_CUT}")

first_take: list[dict] = []
all_take: list[dict] = []

for di, day in enumerate(days):
    day_bars = by_day[day]
    prior_close = first_prior if di == 0 else by_day[days[di - 1]][-1].close
    rth_idx = [i for i, b in enumerate(day_bars) if b.ts.astimezone(ET).time() >= RTH_OPEN]
    if not rth_idx:
        continue
    taken_side: str | None = None

    for i in rth_idx:
        bar = day_bars[i]
        clock = bar.ts.astimezone(ET)
        elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
        if elapsed < 0:
            continue
        cands = setups_for(TICKER, day_bars[: i + 1], orb_minutes=15, prior_close=prior_close)
        if not cands:
            continue
        cands, pb = apply_playbook(cands, TICKER, elapsed)
        fused = fuse(cands, spy_bias=None)
        if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
            continue
        n_fam = len(fused.families or [])
        solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
        score = score_for(fused.conviction, families=n_fam, solo=solo)
        if pb.window == "first_hour" or score < TAKE_CUT:
            continue

        exit_i = min(i + HOLD, len(day_bars) - 1)
        move = (day_bars[exit_i].close - bar.close) / bar.close * 100.0
        signed = move if fused.direction == "call" else -move
        rec = {
            "day": day,
            "time": clock.strftime("%H:%M"),
            "dir": fused.direction,
            "window": pb.window,
            "score": score,
            "move_pct": signed,
            "stack": "+".join(sorted(fused.strategies or [])),
        }
        all_take.append(rec)

        # The desk's own gate: one open row per ticker/direction/day, and the
        # opposite side is refused once a side is live.
        if taken_side is None:
            taken_side = fused.direction
            first_take.append(rec)


def stat(label: str, grp: list[dict]) -> None:
    if not grp:
        print(f"  {label:<28} n=0")
        return
    wins = sum(1 for s in grp if s["move_pct"] > 0)
    avg = sum(s["move_pct"] for s in grp) / len(grp)
    print(
        f"  {label:<28} n={len(grp):<4} win={wins / len(grp) * 100:>3.0f}%  avg={avg:+.3f}%  "
        f"best={max(s['move_pct'] for s in grp):+.3f}  worst={min(s['move_pct'] for s in grp):+.3f}"
    )


print("\n=== what the desk actually alerts (first TAKE per day) ===")
stat("first TAKE of day", first_take)
print("\n=== for contrast: every TAKE the logic emits ===")
stat("all TAKEs", all_take)

print("\n=== first-TAKE by direction ===")
for d in ("call", "put"):
    stat(d, [s for s in first_take if s["dir"] == d])

print("\n=== first-TAKE by window ===")
for w in sorted({s["window"] for s in first_take}):
    stat(w, [s for s in first_take if s["window"] == w])

print("\n=== every first TAKE ===")
print("  day         time   dir   window       score  move%    stack")
for s in first_take:
    print(
        f"  {s['day']}  {s['time']}  {s['dir']:<5} {s['window']:<12} {s['score']:<6} "
        f"{s['move_pct']:+.3f}   {s['stack']}"
    )
