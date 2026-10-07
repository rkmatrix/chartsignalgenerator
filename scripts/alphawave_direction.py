"""Is AlphaWave right about direction at all, before any option cost?

Signed underlying move after each CALL/PUT at fixed horizons, plus the
indicator's own ATR bracket (stop 1.2 ATR, TP1 1.5 ATR, TP2 2.5 ATR).
Compared with what the option round trip needs: ROUND_TRIP / LEVERAGE.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alphawave_edge import load  # noqa: E402
from pa.open_session.alphawave import BAR_MINUTES, SL_ATR, TP1_ATR, TP2_ATR, all_events  # noqa: E402
from pa.open_session.bandit import LEVERAGE, ROUND_TRIP  # noqa: E402

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"


def main() -> None:
    names = sorted({p.name.split("_")[0] for p in H.glob("*_1m_hist.json")})
    need = ROUND_TRIP / LEVERAGE
    horizons = (5, 15, 30, 60)
    moves: dict[int, list[float]] = defaultdict(list)
    bracket: dict[str, list[float]] = defaultdict(list)
    by_engine: dict[str, list[float]] = defaultdict(list)
    for tk in names:
        bars = load(tk)
        by_ts = {b.ts: b for b in bars}
        rth = sorted((b for b in bars if time(9, 30) <= b.ts.time() < time(16, 0)), key=lambda b: b.ts)
        for ev in all_events(bars):
            if ev.kind not in {"CALL", "PUT"}:
                continue
            sign = 1 if ev.kind == "CALL" else -1
            start = ev.bar_ts + timedelta(minutes=BAR_MINUTES)
            d = ev.bar_ts.date()
            close = datetime.combine(d, time(16, 0), ET)
            if start > close - timedelta(minutes=30):
                continue
            day_path = [b for b in rth if b.ts.date() == d and b.ts >= start]
            if not day_path:
                continue
            for h in horizons:
                at = [b for b in day_path if b.ts >= start + timedelta(minutes=h)]
                if at:
                    mv = (at[0].close - ev.price) / ev.price * 100 * sign
                    moves[h].append(mv)
                    if h == 30:
                        by_engine[ev.engine].append(mv)
            if ev.stop is None or ev.tp1 is None:
                continue
            for label, target in (("tp1", ev.tp1), ("tp2", ev.tp2)):
                out = None
                for b in day_path:
                    if b.ts >= close - timedelta(minutes=20):
                        out = (b.close - ev.price) / ev.price * 100 * sign
                        break
                    hit_stop = b.low <= ev.stop if sign == 1 else b.high >= ev.stop
                    hit_tp = b.high >= target if sign == 1 else b.low <= target
                    if hit_stop:
                        out = (ev.stop - ev.price) / ev.price * 100 * sign
                        break
                    if hit_tp:
                        out = (target - ev.price) / ev.price * 100 * sign
                        break
                if out is None:
                    out = (day_path[-1].close - ev.price) / ev.price * 100 * sign
                bracket[label].append(out)

    print(f"option round trip needs a {need:.3f}% move in our favour just to break even")
    for h in horizons:
        v = moves[h]
        print(f"  +{h:>2}m: n={len(v)} mean {mean(v):+.4f}%  right-way {sum(1 for x in v if x > 0) / len(v) * 100:.1f}%  "
              f"clears cost {sum(1 for x in v if x > need) / len(v) * 100:.1f}%")
    for k, v in by_engine.items():
        print(f"  30m by engine {k}: n={len(v)} mean {mean(v):+.4f}%")
    for k, v in bracket.items():
        opt = [x * LEVERAGE - ROUND_TRIP for x in v]
        print(f"  ATR bracket {k}: n={len(v)} mean move {mean(v):+.4f}%  "
              f"win {sum(1 for x in opt if x > 0) / len(opt) * 100:.1f}%  option mean {mean(opt):+.2f}%")


if __name__ == "__main__":
    main()
