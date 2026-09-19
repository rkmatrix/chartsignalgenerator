"""Sweep the TAKE score cutoff under the desk's real one-entry-per-day rule.

The desk keys a position as {ticker}-{direction}-{day} and blocks the opposite
side, so it enters once per ticker per day at the FIRST signal clearing the
cutoff. Raising the cutoff therefore trades coverage (days with any entry) for
selectivity, and the point of this sweep is to show both, not just win rate.

Scores are computed once per ticker-day and every cutoff is applied as a filter
over that same series, so the sweep is one pass rather than one pass per cutoff.

Sessions are also split into an earlier half and a later half: a cutoff that
only looks good on the half it was chosen from is curve fitting, not an edge.

Usage: python scripts/backtest_cutoff_sweep.py [hold_bars]
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
CUTOFFS = [75, 80, 84, 86, 88, 90, 92]

hist_dir = Path(r"C:\Projects\trading\PA\data\history")


def load(path: Path, ticker: str) -> list[Bar]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
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


# One row per candidate signal: the cutoff filter is applied later.
cands_all: list[dict] = []
day_keys: set[tuple[str, str]] = set()

for path in sorted(hist_dir.glob("*_1m_hist.json")):
    ticker = path.name.split("_")[0].upper()
    bars = load(path, ticker)
    by_day: dict[str, list[Bar]] = {}
    for bar in bars:
        by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)
    days = [
        d
        for d in sorted(by_day)
        if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300
    ]
    print(f"{ticker}: {len(days)} sessions", flush=True)

    for di, day in enumerate(days):
        day_bars = by_day[day]
        prior_close = None if di == 0 else by_day[days[di - 1]][-1].close
        day_keys.add((ticker, day))
        for i, bar in enumerate(day_bars):
            clock = bar.ts.astimezone(ET)
            if clock.time() < RTH_OPEN:
                continue
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
            if pb.window == "first_hour":
                continue
            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            score = score_for(fused.conviction, families=n_fam, solo=solo)
            exit_i = min(i + HOLD, len(day_bars) - 1)
            move = (day_bars[exit_i].close - bar.close) / bar.close * 100.0
            cands_all.append(
                {
                    "ticker": ticker,
                    "day": day,
                    "time": clock.strftime("%H:%M"),
                    "idx": i,
                    "dir": fused.direction,
                    "score": score,
                    "move_pct": move if fused.direction == "call" else -move,
                }
            )

all_days = sorted({d for _, d in day_keys})
mid = all_days[len(all_days) // 2] if all_days else ""
print(f"\n{len(cands_all)} candidate signals over {len(day_keys)} ticker-days")
print(f"out-of-sample split at {mid}\n")


def entries_for(cut: int, rows: list[dict]) -> list[dict]:
    """First signal per ticker-day clearing the cutoff — the desk's rule."""
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["score"] < cut:
            continue
        key = (r["ticker"], r["day"])
        if key not in best or r["idx"] < best[key]["idx"]:
            best[key] = r
    return list(best.values())


def line(label: str, taken: list[dict], universe: int) -> None:
    if not taken:
        print(f"  {label:<12} entries=0")
        return
    wins = sum(1 for s in taken if s["move_pct"] > 0)
    avg = sum(s["move_pct"] for s in taken) / len(taken)
    total = sum(s["move_pct"] for s in taken)
    cover = len(taken) / universe * 100 if universe else 0
    print(
        f"  {label:<12} entries={len(taken):<4} coverage={cover:>3.0f}%  "
        f"win={wins / len(taken) * 100:>3.0f}%  avg={avg:+.4f}%  total={total:+.3f}%"
    )


early_rows = [r for r in cands_all if r["day"] < mid]
late_rows = [r for r in cands_all if r["day"] >= mid]
early_days = len({(t, d) for t, d in day_keys if d < mid})
late_days = len({(t, d) for t, d in day_keys if d >= mid})

print("=== all sessions ===")
for cut in CUTOFFS:
    line(f"cutoff {cut}", entries_for(cut, cands_all), len(day_keys))

print(f"\n=== earlier half (< {mid}) ===")
for cut in CUTOFFS:
    line(f"cutoff {cut}", entries_for(cut, early_rows), early_days)

print(f"\n=== later half (>= {mid}) ===")
for cut in CUTOFFS:
    line(f"cutoff {cut}", entries_for(cut, late_rows), late_days)

print("\n=== per-ticker at current cutoff 75 vs 90 ===")
for cut in (75, 90):
    print(f"  -- cutoff {cut} --")
    taken = entries_for(cut, cands_all)
    for t in sorted({r["ticker"] for r in cands_all}):
        sub = [s for s in taken if s["ticker"] == t]
        universe = len({d for tt, d in day_keys if tt == t})
        line(f"  {t}", sub, universe)
