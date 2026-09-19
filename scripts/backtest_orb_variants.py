"""Test the 15m ORB claims on our own tape instead of taking them on faith.

The usual critique of the 15-minute opening range is that the first breakout is
mostly a trap, and that it only works with a higher-timeframe bias and/or by
fading the failed breakout. That is a testable claim, so test it: 11 tickers x
~20 sessions is a far better sample than the handful of gold trades the claim is
normally argued from.

Variants, all entered on the close of the signal bar and sized in R so the
results do not depend on contract price:

  naive     first 15m close beyond the opening range, stop at the far side
  htf       same, but only in the direction of the 15-minute EMA9/EMA21 trend
  fade      breakout fails (closes back inside the range) -> take the other side
  fade+htf  the fade, but only with the higher-timeframe trend

Usage: python scripts/backtest_orb_variants.py [rr]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
ORB_END = dtime(9, 45)
RR = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5

hist = Path(r"C:\Projects\trading\PA\data\history")


def rth_bars(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for b in payload["bars"]:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        if RTH_OPEN <= ts.time() < RTH_CLOSE:
            out.append({**b, "dt": ts})
    return out


def ema(vals: list[float], n: int) -> list[float | None]:
    k = 2.0 / (n + 1)
    out: list[float | None] = []
    cur = None
    for i, v in enumerate(vals):
        if i < n - 1:
            out.append(None)
            continue
        if cur is None:
            cur = sum(vals[: n]) / n
        else:
            cur = v * k + cur * (1 - k)
        out.append(cur)
    return out


def htf_bias(bars: list[dict]) -> dict[datetime, int]:
    """15-minute EMA9 vs EMA21, carried across sessions. +1 up, -1 down, 0 unknown.

    The bias is stamped with the 15m bar's CLOSE time and only consulted by bars
    after it, so nothing here can see its own future.
    """
    buckets: dict[datetime, list[dict]] = {}
    for b in bars:
        anchor = b["dt"].replace(minute=(b["dt"].minute // 15) * 15, second=0, microsecond=0)
        buckets.setdefault(anchor, []).append(b)
    keys = sorted(buckets)
    closes = [float(buckets[k][-1]["close"]) for k in keys]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    out: dict[datetime, int] = {}
    for i, k in enumerate(keys):
        if e9[i] is None or e21[i] is None:
            out[k] = 0
        else:
            out[k] = 1 if e9[i] > e21[i] else -1
    return out


def bias_at(bias: dict[datetime, int], keys: list[datetime], when: datetime) -> int:
    """Bias from the last 15m bucket that had already CLOSED before `when`.

    Reading the bucket `when` sits inside would leak the future into the entry.
    """
    bucket = when.replace(minute=(when.minute // 15) * 15, second=0, microsecond=0)
    prev = 0
    for k in keys:
        if k >= bucket:
            break
        prev = bias[k]
    return prev


def walk(day: list[dict], start: int, direction: int, entry: float, stop: float) -> float | None:
    """Return the R multiple: +RR on target, -1 on stop, else mark out at the close."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + direction * RR * risk
    for b in day[start + 1:]:
        hi, lo = float(b["high"]), float(b["low"])
        if direction > 0:
            if lo <= stop:
                return -1.0
            if hi >= target:
                return RR
        else:
            if hi >= stop:
                return -1.0
            if lo <= target:
                return RR
    last = float(day[-1]["close"])
    return direction * (last - entry) / risk


results: dict[str, list[dict]] = {k: [] for k in ("naive", "htf", "fade", "fade+htf")}
breakouts = 0
failed = 0
risk_frac: list[float] = []

for path in sorted(hist.glob("*_1m_hist.json")):
    bars = rth_bars(path)
    bias = htf_bias(bars)
    bkeys = sorted(bias)

    by_day: dict[str, list[dict]] = {}
    for b in bars:
        by_day.setdefault(b["dt"].date().isoformat(), []).append(b)

    for day, rows in sorted(by_day.items()):
        if len(rows) < 300:
            continue
        opening = [b for b in rows if b["dt"].time() < ORB_END]
        if len(opening) < 10:
            continue
        hi = max(float(b["high"]) for b in opening)
        lo = min(float(b["low"]) for b in opening)
        if hi <= lo:
            continue
        rest = [b for b in rows if b["dt"].time() >= ORB_END]

        # First close beyond the range.
        bo_i = None
        bo_dir = 0
        for i, b in enumerate(rest):
            c = float(b["close"])
            if c > hi:
                bo_i, bo_dir = i, 1
                break
            if c < lo:
                bo_i, bo_dir = i, -1
                break
        if bo_i is None:
            continue
        breakouts += 1

        entry = float(rest[bo_i]["close"])
        stop = lo if bo_dir > 0 else hi
        tk = path.name.split("_")[0].upper()
        r = walk(rest, bo_i, bo_dir, entry, stop)
        if r is not None:
            results["naive"].append({"r": r, "ticker": tk, "day": day})
            if bias_at(bias, bkeys, rest[bo_i]["dt"]) == bo_dir:
                results["htf"].append({"r": r, "ticker": tk, "day": day})

        # Did that breakout fail back into the range?
        fail_i = None
        for j in range(bo_i + 1, len(rest)):
            c = float(rest[j]["close"])
            if (bo_dir > 0 and c < hi) or (bo_dir < 0 and c > lo):
                fail_i = j
                break
        if fail_i is None:
            continue
        failed += 1
        fdir = -bo_dir
        fentry = float(rest[fail_i]["close"])
        fstop = max(float(b["high"]) for b in rest[bo_i:fail_i + 1]) if fdir < 0 else min(
            float(b["low"]) for b in rest[bo_i:fail_i + 1]
        )
        fr = walk(rest, fail_i, fdir, fentry, fstop)
        if fr is not None:
            risk_frac.append(abs(fentry - fstop) / fentry * 100.0)
            results["fade"].append(
                {"r": fr, "ticker": tk, "day": day, "at": rest[fail_i]["dt"]}
            )
            if bias_at(bias, bkeys, rest[fail_i]["dt"]) == fdir:
                results["fade+htf"].append({"r": fr, "ticker": tk, "day": day})

        # A one-bar signal is a lottery ticket for any poller. How much does the
        # edge decay if we are late to it?
        for d in (1, 2, 3):
            k = fail_i + d
            if k >= len(rest) - 1:
                continue
            e = float(rest[k]["close"])
            if (fdir > 0 and e <= fstop) or (fdir < 0 and e >= fstop):
                continue  # already through the stop; not a trade
            dr = walk(rest, k, fdir, e, fstop)
            if dr is not None:
                results.setdefault(f"fade +{d}bar", []).append(
                    {"r": dr, "ticker": tk, "day": day}
                )

print(f"reward:risk = 1:{RR}\n")
print(f"sessions with a 15m ORB breakout : {breakouts}")
print(f"of those, breakout failed back in: {failed}  ({failed / breakouts * 100:.0f}%)\n")
def line(label: str, rs: list[dict]) -> None:
    if not rs:
        print(f"{label:<14} {0:>5}")
        return
    v = [x["r"] for x in rs]
    wins = sum(1 for r in v if r > 0)
    print(
        f"{label:<14} {len(v):>5} {wins / len(v) * 100:>6.0f}% "
        f"{sum(v) / len(v):>+8.3f} {sum(v):>+9.1f}"
    )


print(f"{'variant':<14} {'n':>5} {'win%':>7} {'avg R':>8} {'total R':>9}")
print("-" * 47)
for name in ("naive", "htf", "fade", "fade+htf", "fade +1bar", "fade +2bar", "fade +3bar"):
    line(name, results.get(name, []))

days = sorted({x["day"] for x in results["naive"]})
mid = days[len(days) // 2] if days else ""
print(f"\n=== out of sample, split at {mid} ===")
print(f"{'variant':<14} {'n':>5} {'win%':>7} {'avg R':>8} {'total R':>9}")
print("-" * 47)
for name in ("naive", "fade"):
    line(f"{name} early", [x for x in results[name] if x["day"] < mid])
    line(f"{name} late", [x for x in results[name] if x["day"] >= mid])

print("\n=== fade, per ticker ===")
print(f"{'ticker':<14} {'n':>5} {'win%':>7} {'avg R':>8} {'total R':>9}")
print("-" * 47)
for t in sorted({x["ticker"] for x in results["fade"]}):
    line(t, [x for x in results["fade"] if x["ticker"] == t])

v = sorted(x["r"] for x in results["fade"])
print(f"\nfade: best trade {v[-1]:+.1f}R, worst {v[0]:+.1f}R")
print(f"fade total without the single best trade: {sum(v[:-1]):+.1f}R")
risk_frac.sort()
print(f"fade stop distance, median {risk_frac[len(risk_frac) // 2]:.2f}% of price")

# The desk forces every first-hour print to WATCH, so where these land in the
# session decides whether wiring the fade in would ever actually trade.
print("\n=== when the fade triggers (desk window) ===")
buckets: dict[str, list[float]] = {}
for x in results["fade"]:
    mins = (x["at"].hour * 60 + x["at"].minute) - (9 * 60 + 30)
    w = (
        "first_hour  <60m" if mins < 60
        else "mid_morning 60-90" if mins < 90
        else "after_90    90-135" if mins < 135
        else "lunch       135-225" if mins < 225
        else "afternoon   225-330" if mins < 330
        else "late        >=330"
    )
    buckets.setdefault(w, []).append(x["r"])
for w in sorted(buckets):
    v = buckets[w]
    wins = sum(1 for r in v if r > 0)
    print(
        f"  {w:<22} n={len(v):<4} {len(v) / len(results['fade']) * 100:>3.0f}% of all  "
        f"win={wins / len(v) * 100:>3.0f}%  total={sum(v):+.1f}R"
    )
