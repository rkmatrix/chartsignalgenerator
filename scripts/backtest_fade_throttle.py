"""Does exempting orb_fade from other_side_block actually pay?

The desk keys a row as {ticker}-{direction}-{day} and refuses the opposite side
once one is live. The ORB fade is the opposite side almost by definition, so
that rule suppresses it. This replays the real pipeline across every ticker,
collects every signal it emits, then applies the throttle policies offline so
both are scored on identical signals.

Forward move over HOLD bars, signed by direction, is the same yardstick the
other harnesses use. It is underlying movement, not option P&L.

Usage: python scripts/backtest_fade_throttle.py [hold_bars] [take_cut]
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
HOLD = int(sys.argv[1]) if len(sys.argv) > 1 else 15
TAKE_CUT = int(sys.argv[2]) if len(sys.argv) > 2 else 75
hist_dir = Path(r"C:\Projects\trading\PA\data\history")

emitted: list[dict] = []

for path in sorted(hist_dir.glob("*_1m_hist.json")):
    ticker = path.name.split("_")[0].upper()
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_prior = payload.get("prior_close")
    bars = [
        Bar(
            ticker=ticker,
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
    days = [
        d for d in sorted(by_day)
        if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300
    ]

    for di, day in enumerate(days):
        day_bars = by_day[day]
        prior_close = first_prior if di == 0 else by_day[days[di - 1]][-1].close
        rth_idx = [
            i for i, b in enumerate(day_bars)
            if b.ts.astimezone(ET).time() >= RTH_OPEN
        ]
        for i in rth_idx:
            bar = day_bars[i]
            clock = bar.ts.astimezone(ET)
            elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
            if elapsed < 0:
                continue
            cands = setups_for(ticker, day_bars[: i + 1], orb_minutes=15, prior_close=prior_close)
            if not cands:
                continue
            cands, pb = apply_playbook(cands, ticker, elapsed)
            fused = fuse(cands, spy_bias=None)
            if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
                continue
            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            score = score_for(fused.conviction, families=n_fam, solo=solo)
            if pb.window == "first_hour":
                verdict = "WATCH"
            elif score >= TAKE_CUT:
                verdict = "TAKE"
            elif score >= 50:
                verdict = "WATCH"
            else:
                continue

            exit_i = min(i + HOLD, len(day_bars) - 1)
            move = (day_bars[exit_i].close - bar.close) / bar.close * 100.0
            emitted.append({
                "ticker": ticker,
                "day": day,
                "i": i,
                "time": clock.strftime("%H:%M"),
                "dir": fused.direction,
                "verdict": verdict,
                "window": pb.window,
                "score": score,
                "move_pct": move if fused.direction == "call" else -move,
                "fade": "orb_fade" in (fused.strategies or []),
            })

emitted.sort(key=lambda s: (s["ticker"], s["day"], s["i"]))
print(f"pipeline emitted {len(emitted)} signals "
      f"({sum(1 for s in emitted if s['fade'])} carrying the fade)\n")


def throttle(rows: list[dict], exempt_fade: bool) -> list[dict]:
    """One row per ticker/direction/day; opposite side refused once a side is live."""
    kept: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    side: dict[tuple[str, str], str] = {}
    for s in rows:
        key = (s["ticker"], s["day"])
        tid = (s["ticker"], s["dir"], s["day"])
        if tid in seen:
            continue
        prior = side.get(key)
        if prior and prior != s["dir"] and not (exempt_fade and s["fade"]):
            continue
        seen.add(tid)
        side.setdefault(key, s["dir"])
        kept.append(s)
    return kept


def stat(label: str, grp: list[dict]) -> None:
    if not grp:
        print(f"  {label:<26} n=0")
        return
    wins = sum(1 for s in grp if s["move_pct"] > 0)
    avg = sum(s["move_pct"] for s in grp) / len(grp)
    print(
        f"  {label:<26} n={len(grp):<4} win={wins / len(grp) * 100:>3.0f}%  "
        f"avg={avg:+.4f}%  sum={sum(s['move_pct'] for s in grp):+.2f}%"
    )


for exempt in (False, True):
    kept = throttle(emitted, exempt_fade=exempt)
    fades = [s for s in kept if s["fade"]]
    print(f"=== other_side_block {'EXEMPTS' if exempt else 'applies to'} the fade ===")
    stat("all signals", kept)
    stat("  fade signals", fades)
    stat("  everything else", [s for s in kept if not s["fade"]])
    stat("  fade TAKEs", [s for s in fades if s["verdict"] == "TAKE"])
    stat("  fade WATCHes", [s for s in fades if s["verdict"] == "WATCH"])
    print()
