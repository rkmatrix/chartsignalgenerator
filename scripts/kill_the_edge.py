"""Try to destroy the two surviving cohorts before trusting either of them.

edge_hunt found 2 survivors from ~1,258 tests. That is fewer than chance alone
would throw up, which is itself a warning. Three attempts to kill them:

  1. PERMUTATION. Shuffle the outcomes so no signal can carry information, and
     re-run the identical search. If noise produces as many survivors, the two
     real ones are indistinguishable from noise.

  2. BETA. Both survivors reduce to "be long in the first hour". If the tape
     simply rose across the sample, that is not skill, it is exposure -- and it
     reverses the day the drift does. Compared against a signal-free control
     that is long the same names in the same window.

  3. TICKER CONCENTRATION. An edge carried by one name in one week is an
     accident of that name, not a rule.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime
from itertools import product
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pa.open_session.bandit import LEVERAGE, ROUND_TRIP, bucket_for  # noqa: E402

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"
BAR = ROUND_TRIP / LEVERAGE
HOLD = 30


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, cur = [], None
    for v in vals:
        cur = v if cur is None else v * k + cur * (1 - k)
        out.append(cur)
    return out


def load():
    book = json.loads((H / "exit_paths.json").read_text(encoding="utf-8"))
    signals = book["signals"]
    series = {}
    for p in H.glob("*_1m_hist.json"):
        tk = p.name.split("_")[0].upper()
        rows = []
        for x in json.loads(p.read_text(encoding="utf-8"))["bars"]:
            ts = datetime.fromisoformat(x["ts"]).astimezone(ET)
            rows.append((ts.strftime("%Y-%m-%d %H:%M"), float(x["close"])))
        rows.sort()
        series[tk] = rows
    out = []
    for tk, rows in series.items():
        closes = [c for _, c in rows]
        idx = {k: i for i, (k, _) in enumerate(rows)}
        e12, e26 = ema(closes, 12), ema(closes, 26)
        line = [a - c for a, c in zip(e12, e26)]
        hist = [a - b for a, b in zip(line, ema(line, 9))]
        for s in signals:
            if s["tk"] != tk or len(s["path"]) < HOLD:
                continue
            i = idx.get(f"{s['day']} {s['time']}")
            if i is None or i < 16:
                continue
            sgn = 1.0 if s["dir"] == "call" else -1.0
            # raw = the underlying's own move, direction-agnostic. Needed for the
            # beta control, which must not inherit the signal's sign.
            raw = (closes[min(i + HOLD, len(closes) - 1)] - closes[i]) / closes[i] * 100.0
            out.append({
                "day": s["day"], "tk": tk, "dir": s["dir"],
                "window": s.get("window") or "", "bucket": bucket_for(tk),
                "score": float(s.get("score") or 0),
                "tape": sgn * (closes[i] - closes[i - 15]) / closes[i - 15] > 0,
                "macd": hist[i] * sgn > 0,
                "move": s["path"][HOLD - 1], "raw": raw,
            })
    return out


def search(rows, cut, min_n=40):
    dims = {
        "window": [None] + sorted({r["window"] for r in rows if r["window"]}),
        "bucket": [None] + sorted({r["bucket"] for r in rows}),
        "dir": [None, "call", "put"],
        "tape": [None, True, False],
        "macd": [None, True, False],
        "score": [None, "hi", "lo"],
    }
    found = 0
    for combo in product(*dims.values()):
        spec = dict(zip(dims.keys(), combo))
        sel = rows
        for key, val in spec.items():
            if val is None:
                continue
            if key == "score":
                sel = [r for r in sel if (r["score"] >= 85) == (val == "hi")]
            else:
                sel = [r for r in sel if r[key] == val]
        early = [r for r in sel if r["day"] < cut]
        late = [r for r in sel if r["day"] >= cut]
        if len(early) < min_n or len(late) < min_n:
            continue
        for sign in (1.0, -1.0):
            me = sign * mean(r["move"] for r in early)
            ml = sign * mean(r["move"] for r in late)
            if me > BAR and ml > BAR:
                found += 1
    return found


def main() -> None:
    rows = load()
    days = sorted({r["day"] for r in rows})
    cut = days[len(days) // 2]
    real = search(rows, cut)
    print(f"{len(rows)} signals, {len(days)} sessions, hold={HOLD}m, bar={BAR:.4f}%")
    print(f"survivors on the real data: {real}\n")

    print("=== 1. permutation test: same search on shuffled outcomes ===")
    rng = random.Random(7)
    counts = []
    for trial in range(20):
        shuffled = [dict(r) for r in rows]
        moves = [r["move"] for r in shuffled]
        rng.shuffle(moves)
        for r, m in zip(shuffled, moves):
            r["move"] = m
        counts.append(search(shuffled, cut))
    print(f"  shuffled survivors over 20 trials: {counts}")
    print(f"  mean {mean(counts):.1f}, max {max(counts)}")
    beats = sum(1 for c in counts if c >= real)
    print(f"  trials matching or beating the real {real}: {beats}/20  -> p ~ {beats / 20:.2f}")
    if beats / 20 > 0.05:
        print("  VERDICT: noise. Pure chance reproduces this as often.\n")
    else:
        print("  VERDICT: survives permutation.\n")

    print("=== 2. beta control: is it just 'the tape rose'? ===")
    fh = [r for r in rows if r["window"] == "first_hour"]
    # Signal-free control: every first-hour observation, held long regardless of
    # what the engine said. If this matches the 'edge', the edge is exposure.
    ctrl = mean(r["raw"] for r in fh)
    print(f"  first-hour signals: {len(fh)}")
    print(f"  long-everything control move: {ctrl:+.4f}%  (bar {BAR:.4f}%)")
    for label, sel in (
        ("fade first_hour puts macd=True", [r for r in fh if r["dir"] == "put" and r["macd"]]),
        ("follow first_hour mega calls macd=True",
         [r for r in fh if r["dir"] == "call" and r["macd"] and r["bucket"] == "mega"]),
    ):
        if not sel:
            continue
        # Both cohorts are long exposure; compare like with like.
        edge = mean(r["raw"] for r in sel)
        print(f"  {label:<40} n={len(sel):<5} underlying {edge:+.4f}%  vs control {ctrl:+.4f}%")
    print()

    print("=== 3. ticker concentration ===")
    for label, sel in (
        ("fade first_hour puts macd=True", [r for r in fh if r["dir"] == "put" and r["macd"]]),
        ("follow first_hour mega calls macd=True",
         [r for r in fh if r["dir"] == "call" and r["macd"] and r["bucket"] == "mega"]),
    ):
        if not sel:
            continue
        sign = -1.0 if "fade" in label else 1.0
        by_tk = {}
        for r in sel:
            by_tk.setdefault(r["tk"], []).append(sign * r["move"])
        pos = sum(1 for v in by_tk.values() if mean(v) > BAR)
        print(f"  {label}")
        print(f"    {pos}/{len(by_tk)} tickers clear the bar on their own")
        for tk, v in sorted(by_tk.items(), key=lambda kv: -mean(kv[1]))[:6]:
            print(f"      {tk:<6} n={len(v):<4} {mean(v):+.4f}%")


if __name__ == "__main__":
    main()
