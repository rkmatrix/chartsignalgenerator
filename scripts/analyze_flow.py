"""Does options flow predict direction well enough to pay for the contract?

Held to the same bar everything else was held to, because the graveyard of this
project is findings that looked good until they were split by date:

  * measured against the round trip, which costs 0.0796% of the underlying at
    the fitted 72.7x leverage (0.0262% on the most generous defensible spread
    and leverage assumptions). An edge smaller than that is not an edge.
  * reported in both halves of a date split. MACD's +0.0073%, orb_fade and
    "drop the worst decile" all failed exactly here.

Two questions, in increasing order of importance:

  1. Does flow agreement improve the desk's existing signals? Useful but
     bounded: it can only ever filter 4,162 signals that average -0.0015%.
  2. Does flow predict direction ON ITS OWN? This is the one that matters. If
     it does, it is a new signal source rather than another filter on a dead
     one, and the desk has something it has never had.

Flow is z-scored within each ticker, because SPY's raw volume dwarfs XOM's and
an unnormalised threshold would simply select for liquid names.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ET = ZoneInfo("America/New_York")
HIST = Path(r"C:\Projects\trading\PA\data\history")
FLOW = HIST / "flow"
HORIZON = 15
TRAIL = 15  # minutes of flow accumulated before a reading is taken
BAR_FITTED = 0.0796
BAR_FAVORABLE = 0.0262
METRICS = ("net_delta", "net_call_premium", "net_call_volume")


def load_flow() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(FLOW.glob("*_flow.json")):
        tk = p.name.split("_")[0].upper()
        try:
            out[tk] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_bars() -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for p in HIST.glob("*_1m_hist.json"):
        tk = p.name.split("_")[0].upper()
        rows = []
        for b in json.loads(p.read_text(encoding="utf-8"))["bars"]:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            rows.append((ts.strftime("%Y-%m-%d %H:%M"), float(b["close"])))
        rows.sort()
        out[tk] = rows
    return out


def trailing(flow_day: dict[str, dict], key: str, metric: str) -> float | None:
    """Sum one flow metric over the TRAIL minutes ending at key."""
    try:
        ts = datetime.strptime(key, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    total = 0.0
    seen = 0
    for k in range(TRAIL):
        stamp = (ts - timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M")
        rec = flow_day.get(stamp)
        if rec and metric in rec:
            total += rec[metric]
            seen += 1
    return total if seen >= TRAIL // 2 else None


def zscore(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    mu = mean(values)
    sd = pstdev(values) or 1.0
    return mu, sd


def split_report(label: str, rows: list[tuple[str, float]], bar: float) -> None:
    """rows are (day, signed_forward_move)."""
    if len(rows) < 60:
        print(f"  {label:<34} n={len(rows)} too few")
        return
    days = sorted({d for d, _ in rows})
    cut = days[len(days) // 2]
    e = [m for d, m in rows if d < cut]
    l = [m for d, m in rows if d >= cut]
    m_all, m_e, m_l = mean(m for _, m in rows), mean(e), mean(l)
    consistent = (m_e > bar) and (m_l > bar)
    flag = "CLEARS BOTH HALVES" if consistent else ("clears overall" if m_all > bar else "")
    print(f"  {label:<34} {len(rows):>6} {m_all:>9.4f} {m_e:>9.4f} {m_l:>9.4f}  {flag}")


def main() -> None:
    flow = load_flow()
    bars = load_bars()
    if not flow:
        print("no flow data yet")
        return
    print(f"flow loaded for {len(flow)} tickers\n")

    # ---------------------------------------------------- question 1
    blob = json.loads((HIST / "exit_paths.json").read_text(encoding="utf-8"))
    sigs = blob["signals"]
    agree: list[tuple[str, float]] = []
    disagree: list[tuple[str, float]] = []
    for s in sigs:
        tk = s["tk"]
        if tk not in flow or len(s["path"]) < HORIZON:
            continue
        day_flow = flow[tk].get(s["day"])
        if not day_flow:
            continue
        val = trailing(day_flow, f"{s['day']} {s['time']}", "net_delta")
        if val is None:
            continue
        bullish = val > 0
        wants_up = s["dir"] == "call"
        (agree if bullish == wants_up else disagree).append((s["day"], s["path"][HORIZON - 1]))

    print("=== Q1: does flow agree with the desk's existing signals? ===")
    print(f"  {'cohort':<34} {'n':>6} {'mean':>9} {'early':>9} {'late':>9}")
    split_report("flow AGREES with signal", agree, BAR_FITTED)
    split_report("flow DISAGREES with signal", disagree, BAR_FITTED)

    # ---------------------------------------------------- question 2
    print("\n=== Q2: does flow predict direction on its own? ===")
    print("every minute of every session ranked by trailing flow, forward 15m move")
    for metric in METRICS:
        samples: list[tuple[str, str, float, float]] = []  # tk, day, z-input, fwd
        for tk, days in flow.items():
            if tk not in bars:
                continue
            idx = {k: i for i, (k, _) in enumerate(bars[tk])}
            raw: list[tuple[str, float, float]] = []
            for day, day_flow in days.items():
                for key in day_flow:
                    i = idx.get(key)
                    if i is None or i + HORIZON >= len(bars[tk]):
                        continue
                    val = trailing(day_flow, key, metric)
                    if val is None:
                        continue
                    here = bars[tk][i][1]
                    fwd = (bars[tk][i + HORIZON][1] - here) / here * 100.0
                    raw.append((day, val, fwd))
            if len(raw) < 100:
                continue
            mu, sd = zscore([v for _, v, _ in raw])
            for day, val, fwd in raw:
                samples.append((tk, day, (val - mu) / sd, fwd))

        if not samples:
            continue
        print(f"\n  --- {metric} ---")
        print(f"  {'cohort':<34} {'n':>6} {'mean':>9} {'early':>9} {'late':>9}")
        # Treat a strong reading as a directional call: long when very positive,
        # short when very negative, and sign the forward move accordingly.
        for z in (1.0, 1.5, 2.0):
            longs = [(d, f) for _, d, s, f in samples if s >= z]
            shorts = [(d, -f) for _, d, s, f in samples if s <= -z]
            split_report(f"z>=+{z} (long)", longs, BAR_FITTED)
            split_report(f"z<=-{z} (short)", shorts, BAR_FITTED)
            split_report(f"both sides combined, z={z}", longs + shorts, BAR_FITTED)

    print(f"\nbar to clear: {BAR_FITTED:+.4f}% fitted, {BAR_FAVORABLE:+.4f}% most favorable")


if __name__ == "__main__":
    main()
