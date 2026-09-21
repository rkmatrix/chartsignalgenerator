"""Does trade LOCATION separate our signals, as the Kmer strategy insists?

Its second pillar is that location decides everything: never trade in the middle
of a range, only where price has moved outside the Value Area into a discount,
at the 0.705-0.886 Fibonacci pocket measured from the recent swing.

Unlike the footprint confirmation, this is fully computable from 1-minute bars
with volume. Both constructions are built strictly causally -- the volume
profile uses only bars up to the signal, never the finished session, since the
Value Area of a completed day is not knowable while trading it.

Measures whether signals fired in a discount location beat those fired mid-range
by enough to clear each name's own round-trip bar.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.bandit import LEVERAGE  # noqa: E402

ET = ZoneInfo("America/New_York")
H = ROOT / "data" / "history"

ROUND_TRIP = {
    "SPY": 0.97, "QQQ": 1.70, "IWM": 2.84, "TSLA": 3.02, "PLTR": 3.13,
    "NFLX": 3.56, "NVDA": 4.43, "META": 4.44, "BAC": 6.25, "AAPL": 8.24,
    "DIA": 9.26, "AMZN": 9.32, "JPM": 13.06, "MSFT": 14.85, "XOM": 15.32,
    "AMD": 16.36, "GOOGL": 16.53,
}
SWING_LOOKBACK = 60


def session_bars() -> dict[str, dict[str, list[dict]]]:
    """ticker -> day -> ordered RTH bars with price and volume."""
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for p in H.glob("*_1m_hist.json"):
        tk = p.name.split("_")[0].upper()
        for b in json.loads(p.read_text(encoding="utf-8"))["bars"]:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            out[tk][ts.strftime("%Y-%m-%d")].append(
                {
                    "t": ts.strftime("%H:%M"),
                    "c": float(b["close"]),
                    "h": float(b.get("high") or b["close"]),
                    "l": float(b.get("low") or b["close"]),
                    "v": float(b.get("volume") or 0.0),
                }
            )
    for tk in out:
        for day in out[tk]:
            out[tk][day].sort(key=lambda r: r["t"])
    return out


def value_area(bars: list[dict]) -> tuple[float, float, float] | None:
    """POC and the 70% Value Area from a volume profile of the bars so far."""
    if len(bars) < 20:
        return None
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    if hi <= lo:
        return None
    n_bins = 40
    width = (hi - lo) / n_bins
    bins = [0.0] * n_bins
    for b in bars:
        idx = min(int((b["c"] - lo) / width), n_bins - 1)
        bins[idx] += b["v"]
    total = sum(bins)
    if total <= 0:
        return None
    poc_i = max(range(n_bins), key=lambda i: bins[i])
    # Grow outward from the POC until 70% of volume is enclosed.
    lo_i = hi_i = poc_i
    acc = bins[poc_i]
    while acc < total * 0.70 and (lo_i > 0 or hi_i < n_bins - 1):
        down = bins[lo_i - 1] if lo_i > 0 else -1.0
        up = bins[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += max(up, 0.0)
        else:
            lo_i -= 1
            acc += max(down, 0.0)
    return lo + poc_i * width, lo + lo_i * width, lo + (hi_i + 1) * width


def fib_zone(bars: list[dict], direction: str) -> float | None:
    """Retracement of the recent swing, 0 at the extreme price came from."""
    window = bars[-SWING_LOOKBACK:]
    if len(window) < 20:
        return None
    hi = max(b["h"] for b in window)
    lo = min(b["l"] for b in window)
    if hi <= lo:
        return None
    px = bars[-1]["c"]
    # A long is discounted near the swing low; a short near the swing high.
    return (hi - px) / (hi - lo) if direction == "call" else (px - lo) / (hi - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=40)
    args = ap.parse_args()

    book = json.loads((H / "exit_paths.json").read_text(encoding="utf-8"))
    bars = session_bars()

    rows = []
    for s in book["signals"]:
        if len(s["path"]) < args.hold:
            continue
        day_bars = bars.get(s["tk"], {}).get(s["day"]) or []
        upto = [b for b in day_bars if b["t"] <= s["time"]]
        if len(upto) < 30:
            continue
        va = value_area(upto)
        if not va:
            continue
        poc, val, vah = va
        px = upto[-1]["c"]
        fib = fib_zone(upto, s["dir"])
        if fib is None:
            continue
        outside = px < val if s["dir"] == "call" else px > vah
        rows.append({
            "tk": s["tk"], "day": s["day"], "dir": s["dir"],
            "outside_va": outside,
            "in_pocket": 0.705 <= fib <= 0.886,
            "beyond_886": fib > 0.886,
            "move": s["path"][args.hold - 1],
        })

    if not rows:
        print("no signals with computable location")
        return
    days = sorted({r["day"] for r in rows})
    cut = days[len(days) // 2]
    print(f"{len(rows)} signals with location, {len(days)} sessions, hold={args.hold}m")
    print(f"split at {cut}\n")

    def report(label: str, sel: list[dict]) -> None:
        early = [r["move"] for r in sel if r["day"] < cut]
        late = [r["move"] for r in sel if r["day"] >= cut]
        if len(early) < args.min_n or len(late) < args.min_n:
            print(f"  {label:<44} n={len(sel):<5} too few")
            return
        me, ml = mean(early), mean(late)
        same = "consistent" if (me > 0) == (ml > 0) else "flips"
        print(f"  {label:<44} n={len(sel):<5} early={me:+.4f}% late={ml:+.4f}%  {same}")

    print("=== does location separate the signals? ===")
    report("all signals", rows)
    report("outside the Value Area (discount)", [r for r in rows if r["outside_va"]])
    report("inside the Value Area (mid-range)", [r for r in rows if not r["outside_va"]])
    report("in the 0.705-0.886 pocket", [r for r in rows if r["in_pocket"]])
    report("beyond 0.886 (strategy says invalid)", [r for r in rows if r["beyond_886"]])
    report("discount AND in the pocket", [r for r in rows if r["outside_va"] and r["in_pocket"]])

    print("\n=== does any location clear its own bar in both halves? ===")
    print(f"  {'ticker':<7} {'location':<22} {'n':>5} {'early':>9} {'late':>9} {'bar':>8}")
    hits = 0
    for tk in sorted({r["tk"] for r in rows}):
        bar = ROUND_TRIP.get(tk, 99.0) / LEVERAGE
        for label, pred in (
            ("outside VA", lambda r: r["outside_va"]),
            ("outside VA + pocket", lambda r: r["outside_va"] and r["in_pocket"]),
        ):
            sel = [r for r in rows if r["tk"] == tk and pred(r)]
            e = [r["move"] for r in sel if r["day"] < cut]
            la = [r["move"] for r in sel if r["day"] >= cut]
            if len(e) < args.min_n or len(la) < args.min_n:
                continue
            me, ml = mean(e), mean(la)
            ok = me > bar and ml > bar
            if ok:
                hits += 1
            print(
                f"  {tk:<7} {label:<22} {len(sel):>5} {me:>+8.4f}% {ml:>+8.4f}%"
                f" {bar:>7.4f}%  {'CLEARS' if ok else ''}"
            )
    print(f"\n  {hits} cohort(s) clear their own bar in both halves")


if __name__ == "__main__":
    main()
