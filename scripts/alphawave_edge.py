"""Does AlphaWave itself have an edge after option costs?

Every CALL/PUT the indicator prints across the 1-minute history, held to its
own take-profit, to the -25% plan stop, or to 20 minutes before the close,
whichever comes first. Underlying move becomes option return through the
leverage and round-trip cost fitted on live-feed trades (bandit.LEVERAGE,
bandit.ROUND_TRIP). An estimate: it carries no per-contract spread, which is
the very thing that sank 2026-09-30, so real fills will do worse.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.open_session.alphawave import BAR_MINUTES, all_events
from pa.open_session.bandit import LEVERAGE, ROUND_TRIP
from pa.open_session.bars import _rows_to_bars

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"
STOP = -25.0
NO_ENTRY_LAST_MIN = 30
FLATTEN_MIN = 20


def load(tk: str):
    seen = {}
    for name in (f"{tk}_1m_hist.json", f"{tk}_1m_today.json"):
        p = H / name
        if p.exists():
            for r in json.loads(p.read_text(encoding="utf-8")).get("bars") or []:
                seen[r["ts"]] = r
    return _rows_to_bars(tk, list(seen.values()))


def opt(move_pct: float) -> float:
    return move_pct * LEVERAGE - ROUND_TRIP


def simulate(tk: str, bars, skip_open_bar: bool) -> list[tuple]:
    out = []
    evs = all_events(bars)
    rth = [b for b in bars if time(9, 30) <= b.ts.time() < time(16, 0)]
    for i, ev in enumerate(evs):
        if ev.kind not in {"CALL", "PUT"}:
            continue
        # The signal is known when its 5m bar closes.
        start = ev.bar_ts + timedelta(minutes=BAR_MINUTES)
        d = ev.bar_ts.date()
        close = datetime.combine(d, time(16, 0), ET)
        if start > close - timedelta(minutes=NO_ENTRY_LAST_MIN):
            continue
        if skip_open_bar and ev.bar_ts.time() == time(9, 30):
            continue
        sign = 1 if ev.kind == "CALL" else -1
        tp = next(
            (e for e in evs[i + 1:] if e.kind in {f"TP_{ev.kind}", "CALL", "PUT"}),
            None,
        )
        tp_at = None if tp is None else tp.bar_ts + timedelta(minutes=BAR_MINUTES)
        flat_at = close - timedelta(minutes=FLATTEN_MIN)
        end = min(t for t in (tp_at, flat_at) if t is not None)
        path = [b for b in rth if start <= b.ts < end and b.ts.date() == d]
        if not path:
            continue
        result, how = None, ""
        for b in path:
            worst = b.low if sign == 1 else b.high
            if opt((worst - ev.price) / ev.price * 100 * sign) <= STOP:
                result, how = STOP, "stop"
                break
        if result is None:
            by_signal = tp is not None and tp_at <= flat_at and tp.bar_ts.date() == d
            exit_px = tp.price if by_signal else path[-1].close
            how = "tp" if by_signal else "flat"
            result = opt((exit_px - ev.price) / ev.price * 100 * sign)
        out.append((tk, d, ev.bar_ts, ev.kind, ev.engine, result, how))
    return out


def report(label: str, trades: list[tuple]) -> None:
    if not trades:
        print(label, "no trades")
        return
    n = len(trades)
    wins = sum(1 for t in trades if t[5] > 0)
    by_day = defaultdict(float)
    for t in trades:
        by_day[t[1]] += t[5]
    green = sum(1 for v in by_day.values() if v > 0)
    print(f"\n{label}: {n} trades, {len(by_day)} sessions, win {wins / n * 100:.1f}%, "
          f"mean {sum(t[5] for t in trades) / n:+.2f}% per trade, green sessions {green}/{len(by_day)}")
    for name, idx in (("engine", 4), ("exit", 6), ("side", 3)):
        agg = defaultdict(list)
        for t in trades:
            agg[t[idx]].append(t[5])
        print(f"  by {name}:", {k: (len(v), round(sum(v) / len(v), 2)) for k, v in agg.items()})
    hours = defaultdict(list)
    for t in trades:
        hours[t[2].strftime("%H")].append(t[5])
    print("  by hour:", {k: (len(v), round(sum(v) / len(v), 2)) for k, v in sorted(hours.items())})
    # Split by date: an edge that is real holds in both halves.
    days = sorted(by_day)
    half = days[len(days) // 2] if days else None
    early = [t[5] for t in trades if t[1] < half]
    late = [t[5] for t in trades if t[1] >= half]
    if early and late:
        print(f"  first half mean {sum(early) / len(early):+.2f}% (n={len(early)}), "
              f"second half {sum(late) / len(late):+.2f}% (n={len(late)})")


def main() -> None:
    names = sorted({p.name.split("_")[0] for p in H.glob("*_1m_hist.json")})
    if len(sys.argv) > 1:
        names = [n for n in names if n in sys.argv[1:]]
    allt, no_open = [], []
    for tk in names:
        bars = load(tk)
        allt += simulate(tk, bars, skip_open_bar=False)
        no_open += simulate(tk, bars, skip_open_bar=True)
    print(f"names: {len(names)}; leverage {LEVERAGE}, round trip {ROUND_TRIP}%")
    report("AlphaWave as written", allt)
    report("without the 09:30 bar", no_open)


if __name__ == "__main__":
    main()
