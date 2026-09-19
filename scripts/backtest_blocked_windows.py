"""Is the midday blackout earning its keep?

playbook_for returns an empty allow-set for `lunch` and `late`, so the desk
takes no new entries between 11:45 and 13:15 or after 15:00. That rule has
never been measured: apply_playbook drops those candidates before the backtest
sees them, so the blackout is an assumption, not a finding.

Test it by remapping the two blocked windows onto the continuation playbook
that the adjacent windows already use (lunch -> after_90, late -> afternoon),
then reading the result against the windows the desk does trade. Comparing
windows to each other keeps this honest: every window pays the same option
spread, so a relative verdict does not need a leverage model.

Usage: python scripts/backtest_blocked_windows.py [hold_bars] [take_cut]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session import playbook as pbmod  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
HOLD = int(sys.argv[1]) if len(sys.argv) > 1 else 15
TAKE_CUT = int(sys.argv[2]) if len(sys.argv) > 2 else 75
COOLDOWN = 15

true_window = pbmod.session_window


def unblocked(elapsed: float | None) -> str:
    """The window a candidate would be judged under if midday were open."""
    w = true_window(elapsed)
    return {"lunch": "after_90", "late": "afternoon"}.get(w, w)


# playbook_for reads session_window internally, so patching it here routes the
# blocked windows into the continuation branch without touching the real rules.
pbmod.session_window = unblocked

hist_dir = Path(r"C:\Projects\trading\PA\data\history")
signals: list[dict] = []

for path in sorted(hist_dir.glob("*_1m_hist.json")):
    TICKER = path.name.split("_")[0].upper()
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_prior = payload.get("prior_close")
    bars = [
        Bar(
            ticker=TICKER,
            ts=datetime.fromisoformat(b["ts"]),
            open=float(b["open"]), high=float(b["high"]),
            low=float(b["low"]), close=float(b["close"]),
            volume=float(b["volume"]), timeframe=Timeframe.M1,
        )
        for b in payload["bars"]
    ]
    by_day: dict[str, list[Bar]] = {}
    for bar in bars:
        by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)
    days = [d for d in sorted(by_day)
            if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300]

    for di, day in enumerate(days):
        day_bars = by_day[day]
        prior_close = first_prior if di == 0 else by_day[days[di - 1]][-1].close
        rth_idx = [i for i, b in enumerate(day_bars)
                   if b.ts.astimezone(ET).time() >= RTH_OPEN]
        last_sig = -10_000
        for i in rth_idx:
            bar = day_bars[i]
            clock = bar.ts.astimezone(ET)
            elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
            if elapsed < 0:
                continue
            cands = setups_for(TICKER, day_bars[: i + 1], orb_minutes=15,
                               prior_close=prior_close)
            if not cands:
                continue
            cands, _pb = pbmod.apply_playbook(cands, TICKER, elapsed)
            fused = fuse(cands, spy_bias=None)
            if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
                continue
            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            score = score_for(fused.conviction, families=n_fam, solo=solo)
            actual = true_window(elapsed)
            if actual == "first_hour":
                verdict = "WATCH"
            elif score >= TAKE_CUT:
                verdict = "TAKE"
            elif score >= 50:
                verdict = "WATCH"
            else:
                continue
            if i - last_sig < COOLDOWN:
                continue
            last_sig = i

            entry = bar.close
            j = min(i + HOLD, len(day_bars) - 1)
            move = (day_bars[j].close - entry) / entry * 100.0
            signals.append({
                "ticker": TICKER, "day": day, "window": actual,
                "blocked": actual in {"lunch", "late"},
                "verdict": verdict, "score": score,
                "move_pct": move if fused.direction == "call" else -move,
            })


def stat(label: str, grp: list[dict]) -> None:
    if not grp:
        print(f"  {label:<20} n=0")
        return
    wins = sum(1 for s in grp if s["move_pct"] > 0)
    avg = sum(s["move_pct"] for s in grp) / len(grp)
    print(f"  {label:<20} n={len(grp):<5} win={wins / len(grp) * 100:>3.0f}%  "
          f"avg={avg:+.4f}%  total={sum(s['move_pct'] for s in grp):+8.2f}%")


takes = [s for s in signals if s["verdict"] == "TAKE"]
print(f"\nhold={HOLD}  take_cut={TAKE_CUT}  signals={len(signals)}  TAKEs={len(takes)}")

order = ["first_hour", "mid_morning", "after_90", "lunch", "afternoon", "late"]
print("\n=== TAKE by window (lunch and late are currently refused) ===")
for w in order:
    grp = [s for s in takes if s["window"] == w]
    stat(w + ("  [BLOCKED]" if w in {"lunch", "late"} else ""), grp)

print("\n=== the blackout as a whole ===")
stat("traded windows", [s for s in takes if not s["blocked"]])
stat("blocked windows", [s for s in takes if s["blocked"]])

print("\n=== blocked windows, split out of sample ===")
days_all = sorted({s["day"] for s in takes})
mid = days_all[len(days_all) // 2]
for w in ("lunch", "late"):
    stat(f"{w} early", [s for s in takes if s["window"] == w and s["day"] < mid])
    stat(f"{w} late ", [s for s in takes if s["window"] == w and s["day"] >= mid])

print("\n=== lunch TAKEs per ticker ===")
for t in sorted({s["ticker"] for s in takes if s["window"] == "lunch"}):
    stat(t, [s for s in takes if s["window"] == "lunch" and s["ticker"] == t])
