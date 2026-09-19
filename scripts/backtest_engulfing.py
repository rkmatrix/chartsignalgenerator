"""Does the 1m engulfing scalp survive contact with our own tape?

The claim: an engulfing candle on the 1m is only tradeable with context, namely
a 15m trend bias and a 5m pullback into the 20/50 MA zone. Stop goes beyond the
5m 50 EMA, target about 1.3R.

That is four separate claims, so test them apart as well as together:

  raw        every engulfing candle, no context (the strawman)
  bias       + 15m 20/50 EMA trend agreement
  loc        + price inside the 5m 20/50 EMA pullback zone
  full       + both, which is the strategy as described

Results are in R so contract price cannot flatter them, and the higher
timeframes are read from the last COMPLETED bucket so nothing sees its future.

Usage: python scripts/backtest_engulfing.py [rr]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ET = ZoneInfo("America/New_York")
RTH_OPEN, RTH_CLOSE = dtime(9, 30), dtime(16, 0)
RR = float(sys.argv[1]) if len(sys.argv) > 1 else 1.3
STOP_PAD = 0.0005  # the video insists on giving the 50 EMA some air
hist = Path(r"C:\Projects\trading\PA\data\history")


def ema(vals: list[float], n: int) -> list[float | None]:
    k = 2.0 / (n + 1)
    out: list[float | None] = []
    cur: float | None = None
    for i, v in enumerate(vals):
        if i < n - 1:
            out.append(None)
            continue
        cur = sum(vals[:n]) / n if cur is None else v * k + cur * (1 - k)
        out.append(cur)
    return out


def buckets(bars: list[dict], minutes: int) -> tuple[list[datetime], list[float]]:
    """Aggregate 1m closes into higher-timeframe bars, carried across sessions."""
    grouped: dict[datetime, list[dict]] = {}
    for b in bars:
        dt = b["dt"]
        anchor = dt.replace(minute=(dt.minute // minutes) * minutes, second=0, microsecond=0)
        grouped.setdefault(anchor, []).append(b)
    keys = sorted(grouped)
    return keys, [float(grouped[k][-1]["close"]) for k in keys]


def at(keys: list[datetime], series: list[float | None], when: datetime, minutes: int):
    """Value from the last bucket that had CLOSED before `when`."""
    bucket = when.replace(minute=(when.minute // minutes) * minutes, second=0, microsecond=0)
    val = None
    for i, k in enumerate(keys):
        if k >= bucket:
            break
        val = series[i]
    return val


def engulfing(prev: dict, cur: dict) -> int:
    """+1 bullish, -1 bearish, 0 neither. Body engulfs body."""
    po, pc = float(prev["open"]), float(prev["close"])
    co, cc = float(cur["open"]), float(cur["close"])
    if pc < po and cc > co and co <= pc and cc >= po:
        return 1
    if pc > po and cc < co and co >= pc and cc <= po:
        return -1
    return 0


def walk(bars: list[dict], start: int, d: int, entry: float, stop: float) -> float | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + d * RR * risk
    for b in bars[start + 1:]:
        hi, lo = float(b["high"]), float(b["low"])
        if d > 0:
            if lo <= stop:
                return -1.0
            if hi >= target:
                return RR
        else:
            if hi >= stop:
                return -1.0
            if lo <= target:
                return RR
    return d * (float(bars[-1]["close"]) - entry) / risk


results: dict[str, list[dict]] = {k: [] for k in ("raw", "bias", "loc", "full")}
seen = 0

for path in sorted(hist.glob("*_1m_hist.json")):
    tk = path.name.split("_")[0].upper()
    payload = json.loads(path.read_text(encoding="utf-8"))
    bars = []
    for b in payload["bars"]:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        if RTH_OPEN <= ts.time() < RTH_CLOSE:
            bars.append({**b, "dt": ts})
    if len(bars) < 500:
        continue

    k5, c5 = buckets(bars, 5)
    k15, c15 = buckets(bars, 15)
    e5_20, e5_50 = ema(c5, 20), ema(c5, 50)
    e15_20, e15_50 = ema(c15, 20), ema(c15, 50)

    by_day: dict[str, list[dict]] = {}
    for b in bars:
        by_day.setdefault(b["dt"].date().isoformat(), []).append(b)

    for day, rows in sorted(by_day.items()):
        if len(rows) < 300:
            continue
        for i in range(1, len(rows) - 1):
            d = engulfing(rows[i - 1], rows[i])
            if d == 0:
                continue
            when = rows[i]["dt"]
            f20, f50 = at(k5, e5_20, when, 5), at(k5, e5_50, when, 5)
            h20, h50 = at(k15, e15_20, when, 15), at(k15, e15_50, when, 15)
            if None in (f20, f50, h20, h50):
                continue
            seen += 1

            entry = float(rows[i]["close"])
            # Stop beyond the 5m 50 EMA, as described, but never on the wrong
            # side of the trigger candle itself.
            if d > 0:
                stop = min(float(f50) * (1 - STOP_PAD), float(rows[i]["low"]))
            else:
                stop = max(float(f50) * (1 + STOP_PAD), float(rows[i]["high"]))
            r = walk(rows, i, d, entry, stop)
            if r is None:
                continue

            rec = {
                "r": r, "ticker": tk, "day": day,
                # How far the stop sits, as a share of price. This decides
                # whether the edge survives being expressed in options: a move
                # too small to clear the bid/ask round trip is not tradeable.
                "risk_pct": abs(entry - stop) / entry * 100.0,
                "hour": when.hour,
            }
            results["raw"].append(rec)
            bias_ok = (h20 > h50) if d > 0 else (h20 < h50)
            lo_z, hi_z = min(float(f20), float(f50)), max(float(f20), float(f50))
            loc_ok = lo_z <= entry <= hi_z
            if bias_ok:
                results["bias"].append(rec)
            if loc_ok:
                results["loc"].append(rec)
            if bias_ok and loc_ok:
                results["full"].append(rec)


def line(label: str, rs: list[dict]) -> None:
    if not rs:
        print(f"  {label:<10} n=0")
        return
    v = [x["r"] for x in rs]
    wins = sum(1 for r in v if r > 0)
    print(f"  {label:<10} n={len(v):<5} win={wins / len(v) * 100:>3.0f}%  "
          f"avgR={mean(v):>+7.3f}  totalR={sum(v):>+9.1f}")


print(f"reward:risk 1:{RR}   engulfing candles found: {seen}\n")
print("=== all sessions ===")
for k in ("raw", "bias", "loc", "full"):
    line(k, results[k])

days = sorted({x["day"] for x in results["raw"]})
mid = days[len(days) // 2]
print(f"\n=== out of sample, split at {mid} ===")
for k in ("raw", "bias", "loc", "full"):
    line(f"{k} early", [x for x in results[k] if x["day"] < mid])
    line(f"{k} late", [x for x in results[k] if x["day"] >= mid])

print("\n=== full strategy, per ticker ===")
for t in sorted({x["ticker"] for x in results["full"]}):
    line(t, [x for x in results["full"] if x["ticker"] == t])

print("\n=== is the move big enough to trade in options? ===")
rp = sorted(x["risk_pct"] for x in results["full"])
if rp:
    med = rp[len(rp) // 2]
    print(f"  stop distance: median {med:.3f}% of price, "
          f"p25 {rp[len(rp) // 4]:.3f}%, p75 {rp[int(len(rp) * .75)]:.3f}%")
    print(f"  a {RR}R winner therefore moves the underlying about {med * RR:.3f}%")
    print("  round-trip option spread measured on our book: ~3.8% of premium")

print("\n=== signals per ticker-day (the desk only banks one) ===")
pairs = {(x["ticker"], x["day"]) for x in results["full"]}
print(f"  {len(results['full'])} signals over {len(pairs)} ticker-days "
      f"= {len(results['full']) / max(1, len(pairs)):.1f} per ticker-day")

print("\n=== by hour, ET ===")
for h in sorted({x["hour"] for x in results["full"]}):
    line(f"{h:02d}:00", [x for x in results["full"] if x["hour"] == h])
