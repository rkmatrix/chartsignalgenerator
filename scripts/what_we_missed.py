"""What would today's refused signals actually have returned?

The premise behind loosening the gate is that the desk sat out big winners:
SPY +1.06%, QQQ +1.97%, META +9.44%. But a day's total move is not what a
signal captures. What matters is where each signal fired and what happened
*after* it, which is measurable from the same bars the engine read.

This takes every signal the pipeline printed today and scores the forward move
from the signal bar, so the "missed 500-900%" claim gets checked against entry
timing rather than against the daily range.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for, verdict_for  # noqa: E402
from pa.open_session.playbook import apply_playbook, session_window  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402
from pa.domain.models import Bar, Timeframe  # noqa: E402

ET = ZoneInfo("America/New_York")
H = ROOT / "data" / "history"
COOLDOWN = 15
HOLDS = (15, 30, 60)

TICKERS = ["SPY", "QQQ", "META", "AMZN", "MSFT", "NVDA", "NFLX", "BAC", "DIA", "IWM"]


def load_today(ticker: str) -> list[Bar]:
    p = H / f"{ticker}_1m_today.json"
    if not p.exists():
        return []
    today = datetime.now(ET).strftime("%Y-%m-%d")
    out = []
    for b in json.loads(p.read_text(encoding="utf-8")).get("bars") or []:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        if ts.strftime("%Y-%m-%d") != today or ts.strftime("%H:%M") < "09:30":
            continue
        out.append(Bar(ticker=ticker, timeframe=Timeframe.M1, ts=ts,
                       open=float(b.get("open") or b["close"]), high=float(b.get("high") or b["close"]),
                       low=float(b.get("low") or b["close"]), close=float(b["close"]),
                       volume=float(b.get("volume") or 0.0)))
    return sorted(out, key=lambda x: x.ts)


def signals_for(ticker: str, rth: list[Bar]) -> list[dict]:
    out, last_fire = [], {}
    for i in range(30, len(rth)):
        upto = rth[: i + 1]
        now = upto[-1].ts
        elapsed = (now - now.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60.0
        cands = setups_for(ticker, upto, orb_minutes=15)
        if not cands:
            continue
        kept, _ = apply_playbook(cands, ticker, elapsed)
        if not kept:
            continue
        f = fuse(kept)
        if not f or f.vetoed:
            continue
        n_fam = len(f.families or [])
        solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (f.strategies or []))
        score = score_for(f.conviction, families=n_fam, solo=solo)
        if verdict_for(score) == "SKIP":
            continue
        key = (ticker, f.direction)
        if key in last_fire and (now - last_fire[key]).total_seconds() / 60.0 < COOLDOWN:
            continue
        last_fire[key] = now
        out.append({"i": i, "t": now.strftime("%H:%M"), "dir": f.direction, "score": score,
                    "window": session_window(elapsed) or "", "px": upto[-1].close})
    return out


def main() -> None:
    all_sigs = []
    print("=== every signal the engine printed today, scored by what followed ===\n")
    for tk in TICKERS:
        rth = load_today(tk)
        if len(rth) < 60:
            continue
        sigs = signals_for(tk, rth)
        for s in sigs:
            sign = 1.0 if s["dir"] == "call" else -1.0
            entry = s["px"]
            row = {"tk": tk, **s}
            for hold in HOLDS:
                j = min(s["i"] + hold, len(rth) - 1)
                row[f"m{hold}"] = (rth[j].close - entry) / entry * 100.0 * sign
            # Best case the trade ever offered, which is what a perfect exit gets.
            j_end = min(s["i"] + max(HOLDS), len(rth) - 1)
            seg = rth[s["i"] + 1: j_end + 1] or [rth[s["i"]]]
            if sign > 0:
                row["best"] = (max(b.high for b in seg) - entry) / entry * 100.0
                row["worst"] = (min(b.low for b in seg) - entry) / entry * 100.0
            else:
                row["best"] = (entry - min(b.low for b in seg)) / entry * 100.0
                row["worst"] = (entry - max(b.high for b in seg)) / entry * 100.0
            all_sigs.append(row)

    if not all_sigs:
        print("no signals")
        return

    print(f"  {'ticker':<7} {'time':<6} {'dir':<5} {'score':>5} "
          f"{'+15m':>7} {'+30m':>7} {'+60m':>7} {'best':>7} {'worst':>7}")
    for s in sorted(all_sigs, key=lambda x: x["t"]):
        print(f"  {s['tk']:<7} {s['t']:<6} {s['dir']:<5} {s['score']:>5} "
              f"{s['m15']:>+6.2f}% {s['m30']:>+6.2f}% {s['m60']:>+6.2f}% "
              f"{s['best']:>+6.2f}% {s['worst']:>+6.2f}%")

    print(f"\n=== summary over {len(all_sigs)} signals ===")
    for hold in HOLDS:
        v = [s[f"m{hold}"] for s in all_sigs]
        right = sum(1 for x in v if x > 0)
        print(f"  +{hold:>2}m  mean {mean(v):+6.3f}%   direction right {right}/{len(v)} "
              f"({right / len(v) * 100:.1f}%)")
    print(f"  best case available (perfect exit within 60m): {mean(s['best'] for s in all_sigs):+.2f}%")
    print(f"  worst drawdown first                          : {mean(s['worst'] for s in all_sigs):+.2f}%")

    print("\n=== per ticker, 30-minute forward move ===")
    by = defaultdict(list)
    for s in all_sigs:
        by[s["tk"]].append(s)
    print(f"  {'ticker':<7} {'n':>3} {'mean +30m':>11} {'right':>8} {'mean best':>11}")
    for tk, v in sorted(by.items(), key=lambda kv: -mean(x["m30"] for x in kv[1])):
        right = sum(1 for x in v if x["m30"] > 0)
        print(f"  {tk:<7} {len(v):>3} {mean(x['m30'] for x in v):>+10.3f}% "
              f"{right}/{len(v):<5} {mean(x['best'] for x in v):>+10.2f}%")

    print("\n=== what a 0DTE option does with that ===")
    print("  A near-the-money 0DTE contract runs roughly 8-15x the underlying move")
    print("  before decay. Applying 10x to the average signal:")
    m30 = mean(s["m30"] for s in all_sigs)
    best = mean(s["best"] for s in all_sigs)
    print(f"    average signal, held 30m : {m30:+.3f}% underlying -> about {m30 * 10:+.1f}% on the option")
    print(f"    average signal, perfect exit: {best:+.3f}% underlying -> about {best * 10:+.1f}% on the option")
    print("\n  For a 500% option gain the underlying has to run about +0.50% in your")
    print("  favour and be caught near the top. Share of today's signals that did:")
    for thresh in (0.25, 0.50, 1.00):
        hit = sum(1 for s in all_sigs if s["best"] >= thresh)
        print(f"    best move >= {thresh:.2f}%: {hit}/{len(all_sigs)} ({hit / len(all_sigs) * 100:.0f}%)")


if __name__ == "__main__":
    main()
