"""Does options flow predict forward moves on the names that are cheap to trade?

This is the decisive test for the cost-side thesis. The flow edge was measured
at +0.027% across all tickers, against a blended bar of 0.0797% -- hopeless. But
the bar is per-contract, not blended: SPY round-trips at 0.97% of premium
(bar 0.0134%) and QQQ at 1.70% (bar 0.0234%), while GOOGL costs 16.5%.

So the question is not "does flow predict" in general. It is whether flow
predicts on SPY and QQQ specifically, by more than those names cost to trade.

Discipline, because this is the point where it would be easiest to fool myself:

  * z-scores are strictly causal -- trailing window within the session only,
    never the full-sample mean, which would leak the future into the signal.
  * every result is split by date into halves and must hold in both.
  * a permutation test shuffles the forward returns to establish what this
    search produces from noise alone.
  * each ticker is judged against ITS OWN bar, not an average.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.bandit import LEVERAGE  # noqa: E402

ET = ZoneInfo("America/New_York")
H = ROOT / "data" / "history"

# Median round trip from historical NBBO (scripts/fetch_spreads.py), as % of
# premium. Only names cheap enough to be worth testing are listed.
ROUND_TRIP = {
    "SPY": 0.97, "QQQ": 1.70, "IWM": 2.84, "TSLA": 3.02,
    "PLTR": 3.13, "NFLX": 3.56, "NVDA": 4.43, "META": 4.44,
}
LOOKBACK = 60   # minutes of trailing history for the z-score
METRICS = ("net_delta", "net_call_premium", "net_call_volume")


def bars_for(ticker: str) -> dict[str, float]:
    p = H / f"{ticker}_1m_hist.json"
    if not p.exists():
        return {}
    out = {}
    for b in json.loads(p.read_text(encoding="utf-8"))["bars"]:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        out[ts.strftime("%Y-%m-%d %H:%M")] = float(b["close"])
    return out


def observations(ticker: str, metric: str, horizon: int, thresh: float) -> list[dict]:
    fp = H / "flow" / f"{ticker}_flow.json"
    if not fp.exists():
        return []
    flow = json.loads(fp.read_text(encoding="utf-8"))
    closes = bars_for(ticker)
    if not closes:
        return []

    out: list[dict] = []
    for day, ticks in sorted(flow.items()):
        keys = sorted(ticks.keys())
        vals: list[float] = []
        for i, k in enumerate(keys):
            raw = ticks[k].get(metric)
            if raw is None:
                vals.append(0.0)
                continue
            v = float(raw)
            # Causal z-score: trailing window only.
            if i >= LOOKBACK:
                window = vals[i - LOOKBACK : i]
                mu, sd = mean(window), pstdev(window)
                if sd > 0:
                    z = (v - mu) / sd
                    if abs(z) >= thresh:
                        px_now = closes.get(k)
                        fut_key = _shift(k, horizon)
                        px_fut = closes.get(fut_key)
                        if px_now and px_fut:
                            side = 1.0 if z > 0 else -1.0
                            out.append({
                                "day": day,
                                "ret": side * (px_fut - px_now) / px_now * 100.0,
                            })
            vals.append(v)
    return out


def _shift(key: str, minutes: int) -> str:
    from datetime import timedelta

    ts = datetime.strptime(key, "%Y-%m-%d %H:%M")
    return (ts + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--z", type=float, default=2.0)
    ap.add_argument("--min-n", type=int, default=30)
    args = ap.parse_args()

    print(f"horizon={args.horizon}m  z>={args.z}  lookback={LOOKBACK}m\n")
    print(f"  {'ticker':<7} {'metric':<19} {'n':>5} {'early':>9} {'late':>9} {'bar':>8}  verdict")

    winners = []
    for ticker, rt in sorted(ROUND_TRIP.items(), key=lambda kv: kv[1]):
        bar = rt / LEVERAGE
        for metric in METRICS:
            obs = observations(ticker, metric, args.horizon, args.z)
            if len(obs) < args.min_n * 2:
                continue
            days = sorted({o["day"] for o in obs})
            cut = days[len(days) // 2]
            early = [o["ret"] for o in obs if o["day"] < cut]
            late = [o["ret"] for o in obs if o["day"] >= cut]
            if len(early) < args.min_n or len(late) < args.min_n:
                continue
            me, ml = mean(early), mean(late)
            ok = me > bar and ml > bar
            verdict = "CLEARS BOTH" if ok else ("one half" if (me > bar or ml > bar) else "-")
            print(
                f"  {ticker:<7} {metric:<19} {len(obs):>5} {me:>+8.4f}% {ml:>+8.4f}%"
                f" {bar:>7.4f}%  {verdict}"
            )
            if ok:
                winners.append((ticker, metric, obs, bar))

    print(f"\n=== {len(winners)} cohort(s) clear their own bar in both halves ===")
    if not winners:
        print("  Nothing survives. The cost side alone does not rescue this signal.")
        return

    print("\n=== permutation test on the survivors ===")
    rng = random.Random(11)
    for ticker, metric, obs, bar in winners:
        real = mean(o["ret"] for o in obs)
        rets = [o["ret"] for o in obs]
        beats = 0
        for _ in range(500):
            shuffled = rets[:]
            rng.shuffle(shuffled)
            # Re-sign at random: destroys any link between flow and outcome
            # while preserving the return distribution exactly.
            flipped = [r if rng.random() < 0.5 else -r for r in shuffled]
            if mean(flipped) >= real:
                beats += 1
        p = beats / 500
        note = "SURVIVES" if p < 0.05 else "indistinguishable from noise"
        print(f"  {ticker:<7} {metric:<19} mean={real:+.4f}%  bar={bar:.4f}%  p={p:.3f}  {note}")


if __name__ == "__main__":
    main()
