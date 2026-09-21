"""Walk-forward evaluation of the online learner.

Strictly causal: for each session the model has seen only earlier sessions, it
commits, and only then is it updated with that day's outcomes. Nothing else is
worth reporting -- fitting the whole sample and quoting the fit would flatter
any model, including a worthless one.

Usage:
  python scripts/train_bandit.py [--beta 1.0] [--floor 0.0] [--hold 15]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pa.open_session.bandit import (  # noqa: E402
    OnlineLearner,
    features,
    reward_from_move,
)

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"


def ema(vals: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out: list[float] = []
    cur = None
    for v in vals:
        cur = v if cur is None else v * k + cur * (1 - k)
        out.append(cur)
    return out


def load_signals(hold: int) -> list[dict]:
    """Signals joined to the two readings that carry information, plus reward."""
    book = json.loads((H / "exit_paths.json").read_text(encoding="utf-8"))
    signals = book["signals"]

    series: dict[str, list[tuple[str, float]]] = {}
    for p in H.glob("*_1m_hist.json"):
        tk = p.name.split("_")[0].upper()
        rows = []
        for x in json.loads(p.read_text(encoding="utf-8"))["bars"]:
            ts = datetime.fromisoformat(x["ts"]).astimezone(ET)
            rows.append((ts.strftime("%Y-%m-%d %H:%M"), float(x["close"])))
        rows.sort()
        series[tk] = rows

    out: list[dict] = []
    for tk, rows in series.items():
        closes = [c for _, c in rows]
        idx = {k: i for i, (k, _) in enumerate(rows)}
        e12, e26 = ema(closes, 12), ema(closes, 26)
        line = [a - c for a, c in zip(e12, e26)]
        sig_line = ema(line, 9)
        hist = [a - b for a, b in zip(line, sig_line)]
        for s in signals:
            if s["tk"] != tk or len(s["path"]) < hold:
                continue
            i = idx.get(f"{s['day']} {s['time']}")
            if i is None or i < 16:
                continue
            sgn = 1.0 if s["dir"] == "call" else -1.0
            row = dict(s)
            row["tape_agrees"] = sgn * (closes[i] - closes[i - 15]) / closes[i - 15] > 0
            row["macd_agrees"] = hist[i] * sgn > 0
            # path is already signed for the bet's direction.
            row["reward"] = reward_from_move(s["path"][hold - 1])
            out.append(row)
    out.sort(key=lambda r: (r["day"], r["time"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beta", type=float, default=1.0, help="confidence width")
    ap.add_argument("--floor", type=float, default=0.0, help="edge floor, %% of premium")
    ap.add_argument("--hold", type=int, default=15, help="holding period in minutes")
    ap.add_argument("--half-life", type=float, default=45.0)
    args = ap.parse_args()

    rows = load_signals(args.hold)
    days = sorted({r["day"] for r in rows})
    print(f"{len(rows)} signals over {len(days)} sessions, hold={args.hold}m")
    print(f"beta={args.beta} floor={args.floor} half_life={args.half_life}d\n")

    model = OnlineLearner(half_life_days=args.half_life)
    taken: list[dict] = []
    watched: list[dict] = []
    skipped: list[dict] = []
    per_day: list[tuple[str, int, float]] = []
    prev_day = None

    for day in days:
        todays = [r for r in rows if r["day"] == day]
        if prev_day is not None:
            gap = (datetime.fromisoformat(day) - datetime.fromisoformat(prev_day)).days
            model.decay(float(max(gap, 0)))
        prev_day = day

        # Decide first, using only what earlier sessions taught.
        decisions = []
        for r in todays:
            x = features(r)
            decisions.append((r, x, model.decide(x, beta=args.beta, edge_floor=args.floor)))

        day_take = [r for r, _, d in decisions if d == "TAKE"]
        per_day.append((day, len(day_take), sum(r["reward"] for r in day_take)))
        for r, _, d in decisions:
            (taken if d == "TAKE" else watched if d == "WATCH" else skipped).append(r)

        # Then learn from everything that happened, including what it declined:
        # outcomes of skipped signals are free information and refusing to look
        # at them is how an agent gets stuck in whatever it tried first.
        for r, x, _ in decisions:
            model.update(x, r["reward"])

    def describe(label: str, sel: list[dict]) -> None:
        if not sel:
            print(f"  {label:<26} none")
            return
        wins = sum(1 for r in sel if r["reward"] > 0)
        print(
            f"  {label:<26} n={len(sel):>5}  mean={mean(r['reward'] for r in sel):+7.2f}%"
            f"  win={wins / len(sel) * 100:5.1f}%  total={sum(r['reward'] for r in sel):+9.1f}%"
        )

    print("=== walk-forward outcome (reward is % of premium, after the round trip) ===")
    describe("TAKE (capital)", taken)
    describe("WATCH (recorded only)", watched)
    describe("SKIP (declined)", skipped)
    print()
    describe("baseline: trade everything", rows)
    print()

    green = sum(1 for _, n, p in per_day if n and p > 0)
    acted = sum(1 for _, n, _ in per_day if n)
    print(f"=== sessions ===\n  acted on {acted}/{len(days)}, green {green}/{acted or 1}")
    print(f"  selectivity: TAKE on {len(taken) / len(rows) * 100:.1f}% of signals\n")

    print("=== what it learned (weight, % of premium per unit) ===")
    for name, w in model.top_weights(12):
        print(f"  {name:<26} {w:+7.2f}")


if __name__ == "__main__":
    main()
