"""Walk 1m bars forward and measure what the hunt logic would have printed.

Uses the desk's own setups/playbook/fuse so this tests the shared rules, not a
reimplementation. Reports forward return of the underlying at the plan hold.

Usage: python scripts/backtest_hunt.py [TICKER] [hold_bars] [take_cut]
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
COOLDOWN = 15

hist_dir = Path(r"C:\Projects\trading\PA\data\history")
path = hist_dir / f"{TICKER}_1m_hist.json"
if not path.exists():
    path = hist_dir / f"{TICKER}_1m_today.json"
payload = json.loads(path.read_text(encoding="utf-8"))
raw = payload["bars"]
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
    for b in raw
]
print(f"{TICKER}: {len(bars)} bars  {bars[0].ts.astimezone(ET)}  ->  {bars[-1].ts.astimezone(ET)}")

by_day: dict[str, list[Bar]] = {}
for bar in bars:
    by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)

# Partial sessions (a truncated live day, a half day) skew per-day stats.
full = [d for d in sorted(by_day) if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300]
dropped = [d for d in sorted(by_day) if d not in set(full)]
days = full
print(f"sessions: {len(days)} usable, dropped {dropped}")

signals: list[dict] = []
for di, day in enumerate(days):
    day_bars = by_day[day]
    prior_close = first_prior if di == 0 else by_day[days[di - 1]][-1].close
    rth_idx = [i for i, b in enumerate(day_bars) if b.ts.astimezone(ET).time() >= RTH_OPEN]
    if not rth_idx:
        continue
    last_sig = -10_000
    for i in rth_idx:
        bar = day_bars[i]
        clock = bar.ts.astimezone(ET)
        elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
        if elapsed < 0:
            continue
        window_bars = day_bars[: i + 1]
        cands = setups_for(TICKER, window_bars, orb_minutes=15, prior_close=prior_close)
        if not cands:
            continue
        cands, pb = apply_playbook(cands, TICKER, elapsed)
        fused = fuse(cands, spy_bias=None)
        if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
            continue
        # Same call shape as scan.py:181 — solo gap/burst is scored on the solo curve.
        n_fam = len(fused.families or [])
        solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
        score = score_for(fused.conviction, families=n_fam, solo=solo)
        window = pb.window
        if window == "first_hour":
            verdict = "WATCH"
        elif score >= TAKE_CUT:
            verdict = "TAKE"
        elif score >= 50:
            verdict = "WATCH"
        else:
            verdict = "SKIP"
        if verdict == "SKIP":
            continue
        if i - last_sig < COOLDOWN:
            continue
        last_sig = i

        entry = bar.close
        holds: dict[int, float] = {}
        for h in (5, 15, 30, 60, 120):
            j = min(i + h, len(day_bars) - 1)
            m = (day_bars[j].close - entry) / entry * 100.0
            holds[h] = m if fused.direction == "call" else -m
        exit_i = min(i + HOLD, len(day_bars) - 1)
        exit_px = day_bars[exit_i].close
        move = (exit_px - entry) / entry * 100.0
        signed = move if fused.direction == "call" else -move
        signals.append(
            {
                "day": day,
                "time": clock.strftime("%H:%M"),
                "window": window,
                "dir": fused.direction,
                "verdict": verdict,
                "score": score,
                "fams": len(fused.families or []),
                "stack": "+".join(fused.strategies),
                "entry": round(entry, 2),
                "exit": round(exit_px, 2),
                "move_pct": round(signed, 3),
                "holds": holds,
                "bars_held": exit_i - i,
            }
        )

def stat(label: str, grp: list[dict]) -> None:
    if not grp:
        print(f"  {label:<26} n=0")
        return
    wins = [s for s in grp if s["move_pct"] > 0]
    avg = sum(s["move_pct"] for s in grp) / len(grp)
    print(
        f"  {label:<26} n={len(grp):<4} win={len(wins) / len(grp) * 100:>3.0f}%  "
        f"avg={avg:+.3f}%  best={max(s['move_pct'] for s in grp):+.3f}  "
        f"worst={min(s['move_pct'] for s in grp):+.3f}"
    )


print(f"\nhold={HOLD} bars  take_cut={TAKE_CUT}  cooldown={COOLDOWN}  sessions={len(days)}")

takes = [s for s in signals if s["verdict"] == "TAKE"]
print("\n=== headline ===")
stat("TAKE", takes)
stat("WATCH", [s for s in signals if s["verdict"] == "WATCH"])

print("\n=== TAKE by direction ===")
for d in ("call", "put"):
    stat(d, [s for s in takes if s["dir"] == d])

print("\n=== TAKE by window ===")
for w in sorted({s["window"] for s in takes}):
    stat(w, [s for s in takes if s["window"] == w])

print("\n=== TAKE by family count ===")
for f in sorted({s["fams"] for s in takes}):
    stat(f"{f} families", [s for s in takes if s["fams"] == f])

print("\n=== TAKE by setup present (overlapping) ===")
every = sorted({p for s in signals for p in s["stack"].split("+")})
for name in every:
    stat(name, [s for s in takes if name in s["stack"].split("+")])

print("\n=== per session ===")
for d in days:
    stat(d, [s for s in takes if s["day"] == d])

if days:
    recent = days[-1]
    print(f"\n=== signal detail for {recent} ===")
    print(f"  {'time':<7}{'window':<13}{'dir':<6}{'verdict':<8}{'score':<7}{'fam':<5}{'move%':<9}stack")
    for s in [x for x in signals if x["day"] == recent]:
        print(
            f"  {s['time']:<7}{s['window']:<13}{s['dir']:<6}{s['verdict']:<8}"
            f"{s['score']:<7}{s['fams']:<5}{s['move_pct']:<9}{s['stack']}"
        )

print("\n=== hold sweep (TAKE only) ===")
for h in (5, 15, 30, 60, 120):
    vals = [s["holds"][h] for s in takes]
    if not vals:
        continue
    wins = sum(1 for v in vals if v > 0)
    avg = sum(vals) / len(vals)
    print(f"  {h:>3} bars   win={wins / len(vals) * 100:>3.0f}%  avg={avg:+.4f}%")

print("\n=== score buckets (TAKE+WATCH, ignoring cutoff) ===")
for lo, hi in ((40, 70), (70, 80), (80, 86), (86, 91), (91, 101)):
    grp = [s for s in signals if lo <= s["score"] < hi]
    stat(f"score {lo}-{hi - 1}", grp)
