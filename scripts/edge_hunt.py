"""Exhaustive, disciplined search for any context with positive expectancy.

The learner abstains because every context it saw loses. Before accepting that,
the hypothesis space is widened well past the engine's hand-written setups: every
combination of window, bucket, direction, tape agreement, MACD agreement, score
band and holding period is scored.

Searching thousands of cohorts guarantees some will look profitable by chance,
so a cohort only counts if it is positive in BOTH halves of the sample split by
date, has enough trades in each half to mean anything, and clears the round trip
rather than merely beating zero. That is the same standard that killed orb_fade
and it is applied here to avoid inventing a replacement for it.

Also tests the inverse: if these signals were reliably wrong, fading them would
be an edge, and that would be just as tradeable as being right.
"""

from __future__ import annotations

import json
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
BAR = ROUND_TRIP / LEVERAGE  # underlying move needed to break even


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, cur = [], None
    for v in vals:
        cur = v if cur is None else v * k + cur * (1 - k)
        out.append(cur)
    return out


def load(hold: int) -> list[dict]:
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

    flow = {}
    fp = H / "flow_cache.json"
    if fp.exists():
        try:
            flow = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            flow = {}

    out = []
    for tk, rows in series.items():
        closes = [c for _, c in rows]
        idx = {k: i for i, (k, _) in enumerate(rows)}
        e12, e26 = ema(closes, 12), ema(closes, 26)
        line = [a - c for a, c in zip(e12, e26)]
        hist = [a - b for a, b in zip(line, ema(line, 9))]
        for s in signals:
            if s["tk"] != tk or len(s["path"]) < hold:
                continue
            i = idx.get(f"{s['day']} {s['time']}")
            if i is None or i < 16:
                continue
            sgn = 1.0 if s["dir"] == "call" else -1.0
            out.append(
                {
                    "day": s["day"],
                    "tk": tk,
                    "dir": s["dir"],
                    "window": s.get("window") or "",
                    "bucket": bucket_for(tk),
                    "score": float(s.get("score") or 0),
                    "tape": sgn * (closes[i] - closes[i - 15]) / closes[i - 15] > 0,
                    "macd": hist[i] * sgn > 0,
                    "move": s["path"][hold - 1],
                }
            )
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=40, help="minimum per half")
    args = ap.parse_args()

    rows = load(args.hold)
    days = sorted({r["day"] for r in rows})
    cut = days[len(days) // 2]
    print(f"{len(rows)} signals, {len(days)} sessions, hold={args.hold}m")
    print(f"break-even move {BAR:.4f}%; split at {cut}\n")

    dims = {
        "window": [None] + sorted({r["window"] for r in rows if r["window"]}),
        "bucket": [None] + sorted({r["bucket"] for r in rows}),
        "dir": [None, "call", "put"],
        "tape": [None, True, False],
        "macd": [None, True, False],
        "score": [None, "hi", "lo"],
    }

    survivors = []
    tested = 0
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
        if len(early) < args.min_n or len(late) < args.min_n:
            continue
        tested += 1
        for sign, label in ((1.0, "follow"), (-1.0, "fade")):
            me, ml = sign * mean(r["move"] for r in early), sign * mean(r["move"] for r in late)
            # Must clear the round trip in both halves independently.
            if me > BAR and ml > BAR:
                survivors.append((spec, label, len(sel), me, ml))

    print(f"tested {tested} cohorts with >= {args.min_n} signals in each half")
    print(f"survivors that clear {BAR:.4f}% in BOTH halves: {len(survivors)}\n")

    if survivors:
        for spec, label, n, me, ml in sorted(survivors, key=lambda s: -min(s[3], s[4]))[:20]:
            desc = " ".join(f"{k}={v}" for k, v in spec.items() if v is not None) or "all"
            print(f"  {label:<7} {desc:<52} n={n:<5} early={me:+.4f}% late={ml:+.4f}%")
    else:
        print("  none.\n")
        print("  Best cohorts by the weaker test (positive in both halves, any size):")
        near = []
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
            if len(early) < args.min_n or len(late) < args.min_n:
                continue
            for sign, label in ((1.0, "follow"), (-1.0, "fade")):
                me = sign * mean(r["move"] for r in early)
                ml = sign * mean(r["move"] for r in late)
                if me > 0 and ml > 0:
                    near.append((spec, label, len(sel), me, ml))
        for spec, label, n, me, ml in sorted(near, key=lambda s: -min(s[3], s[4]))[:12]:
            desc = " ".join(f"{k}={v}" for k, v in spec.items() if v is not None) or "all"
            short = min(me, ml) / BAR * 100
            print(
                f"  {label:<7} {desc:<46} n={n:<5} early={me:+.4f}% late={ml:+.4f}%"
                f"  ({short:.0f}% of the bar)"
            )


if __name__ == "__main__":
    main()
