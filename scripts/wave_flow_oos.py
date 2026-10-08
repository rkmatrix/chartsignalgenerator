"""Out-of-sample check of the UW flow filter over every AlphaWave signal since Aug 11.

The book only holds 78 traded signals on four sessions. Here every CALL/PUT
the indicator printed on the 1m history is scored on the underlying (the
indicator's own 1.2 ATR stop vs 1.5 ATR target, and the signed 30-minute
move), split by whether UW net delta over the 30 minutes before the signal
agreed with it, and by time of day. A filter only counts if it wins on most
sessions, not on the pooled total.
"""

from __future__ import annotations

import sys
import time as clock
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alphawave_edge import load  # noqa: E402
from wave_flow import ticks  # noqa: E402

from pa.open_session.alphawave import BAR_MINUTES, all_events  # noqa: E402

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"
FLOW_MINUTES = 30


def _net_delta(ticker: str, at: datetime) -> float | None:
    day = at.strftime("%Y-%m-%d")
    try:
        rows = ticks(ticker, day)
    except Exception:
        clock.sleep(2)
        try:
            rows = ticks(ticker, day)
        except Exception:
            return None
    if not rows:
        return None
    end = at.timestamp()
    start = end - FLOW_MINUTES * 60
    total = 0.0
    for row in rows:
        ts = datetime.fromisoformat(row["tape_time"].replace("Z", "+00:00")).timestamp()
        if start <= ts < end:
            total += float(row.get("net_delta") or 0)
    return total


def _bucket(t: time) -> str:
    if t < time(9, 45):
        return "09:30-09:45"
    if t < time(14, 0):
        return "09:45-14:00"
    return "14:00-15:30"


def main() -> None:
    names = sorted({p.name.split("_")[0] for p in H.glob("*_1m_hist.json")})
    # (bucket, flow) -> list of (day, bracket_r, move30)
    out: dict[tuple[str, str], list[tuple[str, float, float]]] = defaultdict(list)
    for tk in names:
        bars = load(tk)
        rth = sorted((b for b in bars if time(9, 30) <= b.ts.time() < time(16, 0)), key=lambda b: b.ts)
        for ev in all_events(bars):
            if ev.kind not in {"CALL", "PUT"} or ev.stop is None or ev.tp1 is None:
                continue
            start = ev.bar_ts + timedelta(minutes=BAR_MINUTES)
            d = ev.bar_ts.date()
            close = datetime.combine(d, time(16, 0), ET)
            if start > close - timedelta(minutes=30):
                continue
            path = [b for b in rth if b.ts.date() == d and b.ts >= start]
            if not path:
                continue
            sign = 1 if ev.kind == "CALL" else -1
            risk = abs(ev.price - ev.stop)
            r = None
            for b in path:
                if b.ts >= close - timedelta(minutes=20):
                    r = (b.close - ev.price) * sign / risk
                    break
                if (b.low <= ev.stop) if sign == 1 else (b.high >= ev.stop):
                    r = -1.0
                    break
                if (b.high >= ev.tp1) if sign == 1 else (b.low <= ev.tp1):
                    r = abs(ev.tp1 - ev.price) / risk
                    break
            if r is None:
                r = (path[-1].close - ev.price) * sign / risk
            later = [b for b in path if b.ts >= start + timedelta(minutes=30)]
            move = (later[0].close - ev.price) / ev.price * 100 * sign if later else 0.0
            nd = _net_delta(tk, start)
            flow = "no data" if nd is None or nd == 0 else ("agrees" if nd * sign > 0 else "disagrees")
            out[(_bucket(start.time()), flow)].append((d.isoformat(), r, move))

    print(f"R = underlying result in units of the indicator's 1.2 ATR stop (target 1.5 ATR = +1.25R)")
    for key in sorted(out):
        rows = out[key]
        by_day: dict[str, list[float]] = defaultdict(list)
        for day, r, _ in rows:
            by_day[day].append(r)
        green = sum(1 for v in by_day.values() if sum(v) > 0)
        print(
            f"{key[0]} flow {key[1]:<9} n={len(rows):>4} meanR {mean(r for _, r, _ in rows):+.3f} "
            f"win {sum(1 for _, r, _ in rows if r > 0) / len(rows) * 100:4.1f}% "
            f"move30 {mean(m for *_, m in rows):+.4f}% green days {green}/{len(by_day)}"
        )

    print("\nper-session meanR, flow agrees minus disagrees (09:45-14:00):")
    agree = defaultdict(list)
    disagree = defaultdict(list)
    for day, r, _ in out[("09:45-14:00", "agrees")]:
        agree[day].append(r)
    for day, r, _ in out[("09:45-14:00", "disagrees")]:
        disagree[day].append(r)
    better = worse = 0
    for day in sorted(set(agree) & set(disagree)):
        diff = mean(agree[day]) - mean(disagree[day])
        better += diff > 0
        worse += diff <= 0
    print(f"  agrees did better on {better} sessions, worse on {worse}")


if __name__ == "__main__":
    main()
