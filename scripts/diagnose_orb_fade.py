"""Where do ORB-fade candidates die between setups_for() and a printed signal?

The standalone harness finds ~18 fades per ticker, the desk pipeline surfaces 1.
This walks the same tape and counts the exact stage that drops each one, so the
fix is aimed at the real blocker rather than a plausible-sounding one.

Usage: python scripts/diagnose_orb_fade.py [TICKER ...]
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

from pa.domain.models import Bar, Timeframe
from pa.open_session.fuse import fuse
from pa.open_session.grade import score_for, verdict_for
from pa.open_session.playbook import apply_playbook
from pa.open_session.setups import SOLO_STRATEGIES, setups_for

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
COOLDOWN = 15
TAKE_CUT = 75
hist = Path(r"C:\Projects\trading\PA\data\history")

tickers = [t.upper() for t in sys.argv[1:]] or [
    p.name.split("_")[0].upper() for p in sorted(hist.glob("*_1m_hist.json"))
]

overall: Counter[str] = Counter()

for ticker in tickers:
    path = hist / f"{ticker}_1m_hist.json"
    if not path.exists():
        continue
    payload = json.loads(path.read_text(encoding="utf-8"))
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

    stage: Counter[str] = Counter()
    for di, day in enumerate(days):
        day_bars = by_day[day]
        prior_close = None if di == 0 else by_day[days[di - 1]][-1].close
        rth_idx = [
            i for i, b in enumerate(day_bars)
            if b.ts.astimezone(ET).time() >= RTH_OPEN
        ]
        last_sig = -10_000
        seen_fade = False
        for i in rth_idx:
            clock = day_bars[i].ts.astimezone(ET)
            elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
            if elapsed < 0:
                continue
            cands = setups_for(ticker, day_bars[: i + 1], orb_minutes=15, prior_close=prior_close)
            if not cands:
                continue
            fade = [c for c in cands if c.strategy == "orb_fade"]
            # Count one opportunity per day, matching the standalone harness, but
            # keep walking every bar so the cooldown clock stays honest: other
            # signals claim it too, and that is the whole question.
            track = bool(fade) and not seen_fade
            if track:
                seen_fade = True
                stage["1. candidate produced"] += 1

            kept, pb = apply_playbook(cands, ticker, elapsed)
            if not any(c.strategy == "orb_fade" for c in kept):
                if track:
                    stage["2. dropped by playbook"] += 1
                if not kept:
                    continue

            fused = fuse(kept, spy_bias=None) if kept else None
            if fused is None or fused.vetoed or fused.direction not in {"call", "put"}:
                if track and fused is not None and fused.vetoed:
                    stage[f"4. vetoed ({fused.veto_reason})"] += 1
                elif track and fused is None:
                    stage["3. fuse returned nothing"] += 1
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
                verdict = "SKIP"
            if verdict == "SKIP":
                if track:
                    stage["7. scored below SKIP"] += 1
                continue
            if i - last_sig < COOLDOWN:
                if track:
                    stage["8. swallowed by cooldown"] += 1
                continue
            last_sig = i
            if track:
                carried = "orb_fade" in (fused.strategies or [])
                stage[f"9. PRINTED as {verdict}" if carried else "5. printed without the fade"] += 1

    if stage:
        print(f"\n{ticker}")
        for k in sorted(stage):
            print(f"  {k:<34} {stage[k]:>4}")
    overall.update(stage)

print("\n" + "=" * 46)
print("ALL TICKERS")
total = overall.get("1. candidate produced", 0) or 1
for k in sorted(overall):
    print(f"  {k:<34} {overall[k]:>4}  ({overall[k] / total * 100:>3.0f}%)")
