"""Pre-open smoke test: does the desk still find trades with every filter on?

Three gates went in yesterday - the premium band, the MACD veto and the daily
loss stop - and none of them have run together against real bars. Stacking
filters is how a desk goes quiet without anyone noticing, so count what
survives each stage on the most recent sessions before trusting it live.

Usage: python scripts/preopen_check.py [sessions_back]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for  # noqa: E402
from pa.open_session.levels import compute_levels  # noqa: E402
from pa.open_session.playbook import apply_playbook  # noqa: E402
from pa.open_session.scan import macd_block  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 5
COOLDOWN = 15
TAKE_CUT = 75

hist = Path(r"C:\Projects\trading\PA\data\history")
stage = Counter()
per_day: Counter = Counter()
examples: list[str] = []

for path in sorted(hist.glob("*_1m_hist.json")):
    tk = path.name.split("_")[0].upper()
    bars = [
        Bar(ticker=tk, ts=datetime.fromisoformat(b["ts"]),
            open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
            close=float(b["close"]), volume=float(b["volume"]), timeframe=Timeframe.M1)
        for b in json.loads(path.read_text(encoding="utf-8"))["bars"]
    ]
    by_day: dict[str, list[Bar]] = {}
    for bar in bars:
        by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)
    days = [d for d in sorted(by_day)
            if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300][-BACK:]

    for di, day in enumerate(days):
        day_bars = by_day[day]
        rth = [i for i, b in enumerate(day_bars)
               if b.ts.astimezone(ET).time() >= RTH_OPEN]
        last = -10_000
        for i in rth:
            clock = day_bars[i].ts.astimezone(ET)
            elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
            if elapsed < 0 or i - last < COOLDOWN:
                continue
            window = day_bars[: i + 1]
            cands = setups_for(tk, window, orb_minutes=15, prior_close=None)
            if not cands:
                continue
            cands, pb = apply_playbook(cands, tk, elapsed)
            if not cands:
                continue
            fused = fuse(cands, spy_bias=None)
            if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
                continue
            stage["1. fused"] += 1
            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            score = score_for(fused.conviction, families=n_fam, solo=solo)
            if pb.window == "first_hour":
                stage["2. first_hour -> WATCH"] += 1
                continue
            if score < TAKE_CUT:
                stage["3. below take cut"] += 1
                continue
            stage["4. TAKE before MACD"] += 1
            last = i

            lv = compute_levels(tk, window, orb_minutes=15, prior_close=None)
            blocked = macd_block(fused.direction, lv.macd_hist)
            if blocked:
                stage["5. vetoed by MACD"] += 1
                continue
            stage["6. SURVIVES ALL GATES"] += 1
            per_day[day] += 1
            if len(examples) < 8:
                examples.append(
                    f"{day} {clock.strftime('%H:%M')} {tk:<6}{fused.direction:<5}"
                    f"score={score:<4}macd={lv.macd_hist:+.4f}  {'+'.join(fused.strategies)}"
                )

print(f"last {BACK} sessions per ticker, {len(per_day)} distinct sessions\n")
for k in sorted(stage):
    print(f"  {k:<28} {stage[k]}")

survivors = stage["6. SURVIVES ALL GATES"]
before = stage["4. TAKE before MACD"]
print(f"\nMACD keeps {survivors}/{before} = "
      f"{survivors / before * 100:.0f}% of TAKE-grade signals" if before else "\nno TAKEs at all")

print("\n=== signals per session after every gate ===")
for d in sorted(per_day):
    print(f"  {d}  {per_day[d]}")
if per_day:
    print(f"  average {sum(per_day.values()) / len(per_day):.1f} per session "
          f"across {len(per_day)} sessions")

print("\n=== sample of what would fire ===")
for e in examples:
    print("  " + e)
