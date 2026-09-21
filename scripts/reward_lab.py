"""Where, if anywhere, does a trade clear the round trip?

The round trip is a fixed 5.79% of premium, so the underlying has to move about
0.0797% before a position is worth opening at all. This asks two questions the
flat 15-minute mark cannot:

  1. Does holding longer help? The cost is fixed, so a larger move amortises it
     -- but only if the signal has directional edge to grow into. If it has
     none, a longer hold buys variance and nothing else.

  2. Does an exit policy help? Reward measured at a fixed mark is linear in the
     move, which throws away the one structural advantage an option position
     has: a stop truncates the left tail while the right tail runs. That skew is
     real money and a linear reward cannot see it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pa.open_session.bandit import LEVERAGE, ROUND_TRIP  # noqa: E402

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"


def load() -> list[dict]:
    book = json.loads((H / "exit_paths.json").read_text(encoding="utf-8"))
    return book["signals"]


def option_pct(move: float) -> float:
    """Option return for an underlying move, before the round trip."""
    return LEVERAGE * move


def replay(path: list[float], stop_pct: float, target_pct: float, trail: float) -> float:
    """Realised option return under a stop / target / trail, net of the round trip.

    Walks the actual minute path rather than marking at a fixed horizon, so a
    position that ran and gave it back is scored on what it gave back.
    """
    peak = 0.0
    for move in path:
        pnl = option_pct(move)
        peak = max(peak, pnl)
        if pnl <= -stop_pct:
            return -stop_pct - ROUND_TRIP
        if target_pct and pnl >= target_pct:
            return target_pct - ROUND_TRIP
        if trail and peak > 0 and pnl <= peak * trail:
            return peak * trail - ROUND_TRIP
    return option_pct(path[-1]) - ROUND_TRIP if path else -ROUND_TRIP


def main() -> None:
    sigs = load()
    lens = sorted(len(s["path"]) for s in sigs)
    print(f"{len(sigs)} signals; path length median {lens[len(lens) // 2]}m max {lens[-1]}m\n")

    print("=== 1. does holding longer help? (flat mark, no exit policy) ===")
    print(f"  {'hold':>6} {'n':>6} {'mean move':>11} {'mean reward':>12} {'win%':>7}")
    for hold in (5, 15, 30, 60, 90, 120, 180, 240):
        sel = [s for s in sigs if len(s["path"]) >= hold]
        if len(sel) < 100:
            continue
        moves = [s["path"][hold - 1] for s in sel]
        rewards = [option_pct(m) - ROUND_TRIP for m in moves]
        win = sum(1 for r in rewards if r > 0) / len(rewards) * 100
        print(
            f"  {hold:>5}m {len(sel):>6} {mean(moves):>10.4f}% {mean(rewards):>11.2f}% {win:>6.1f}%"
        )

    print("\n=== 2. does an exit policy help? (stop / target / trail) ===")
    print(f"  {'stop':>6} {'target':>7} {'trail':>6} {'n':>6} {'mean':>9} {'win%':>7} {'total':>10}")
    grid = [
        (25, 0, 0.0), (25, 50, 0.0), (35, 0, 0.0), (50, 0, 0.0),
        (25, 0, 0.6), (35, 0, 0.5), (50, 100, 0.5), (35, 75, 0.6),
        (60, 120, 0.5), (100, 0, 0.0),
    ]
    sel = [s for s in sigs if len(s["path"]) >= 30]
    for stop, target, trail in grid:
        rewards = [replay(s["path"], stop, target, trail) for s in sel]
        win = sum(1 for r in rewards if r > 0) / len(rewards) * 100
        print(
            f"  {stop:>5}% {target or '-':>7} {trail or '-':>6} {len(sel):>6}"
            f" {mean(rewards):>8.2f}% {win:>6.1f}% {sum(rewards):>9.0f}%"
        )

    print("\n=== 3. the bar, stated plainly ===")
    need = ROUND_TRIP / LEVERAGE
    print(f"  round trip {ROUND_TRIP:.2f}% of premium / leverage {LEVERAGE:.1f}")
    print(f"  => underlying must move {need:.4f}% before a trade is worth opening")
    for hold in (15, 60, 120):
        sel = [s for s in sigs if len(s["path"]) >= hold]
        if len(sel) < 100:
            continue
        beat = sum(1 for s in sel if s["path"][hold - 1] > need) / len(sel) * 100
        print(f"  at {hold:>3}m: {beat:.1f}% of signals clear it")


if __name__ == "__main__":
    main()
