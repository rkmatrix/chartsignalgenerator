"""Measure the entry edge of a given signal engine, so two versions can be compared.

The working tree carries uncommitted changes to setups/fuse/scan/levels/playbook
made after the last profitable week. The decline was attributed to the option
feed switching from 15-minute-delayed quotes to real-time ones, but that was
never tested against the competing explanation: that these edits made the engine
worse. This runs either version over identical bars and reports the only number
that decides it.

Edge is reported as the mean signed move of the underlying at a fixed horizon,
which is model-free, next to the round-trip cost the option has to clear.

Usage: python scripts/ab_engine.py <src_dir> <label>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

SRC = sys.argv[1] if len(sys.argv) > 1 else r"C:\Projects\trading\PA\src"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "current"
sys.path.insert(0, SRC)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for  # noqa: E402
from pa.open_session.playbook import apply_playbook  # noqa: E402
from pa.open_session.setups import setups_for  # noqa: E402

try:
    from pa.open_session.setups import SOLO_STRATEGIES
except ImportError:  # the pre-change engine had no solo curve
    SOLO_STRATEGIES = frozenset()

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
HIST = Path(r"C:\Projects\trading\PA\data\history")
COOLDOWN = 15
HORIZON = 15
# Same eight names for both engines; the full seventeen doubles runtime for no
# extra discrimination.
TICKERS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META"]


def main() -> None:
    rows: list[dict] = []
    for tk in TICKERS:
        path = HIST / f"{tk}_1m_hist.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        bars = [
            Bar(
                ticker=tk,
                ts=datetime.fromisoformat(b["ts"]),
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                volume=float(b["volume"]),
                timeframe=Timeframe.M1,
            )
            for b in payload["bars"]
        ]
        by_day: dict[str, list[Bar]] = {}
        for bar in bars:
            by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)
        days = [
            d for d in sorted(by_day)
            if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300
        ]
        for di, day in enumerate(days):
            db = by_day[day]
            prior = payload.get("prior_close") if di == 0 else by_day[days[di - 1]][-1].close
            last_sig = -10_000
            for i, bar in enumerate(db):
                clock = bar.ts.astimezone(ET)
                if clock.time() < RTH_OPEN:
                    continue
                elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
                if i + HORIZON >= len(db):
                    continue
                cands = setups_for(tk, db[: i + 1], orb_minutes=15, prior_close=prior)
                if not cands:
                    continue
                cands, pb = apply_playbook(cands, tk, elapsed)
                fused = fuse(cands, spy_bias=None)
                if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
                    continue
                n_fam = len(fused.families or [])
                solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
                try:
                    score = score_for(fused.conviction, families=n_fam, solo=solo)
                except TypeError:  # older signature
                    score = score_for(fused.conviction, families=n_fam)
                if score < 50:
                    continue
                if i - last_sig < COOLDOWN:
                    continue
                last_sig = i
                entry = bar.close
                sign = 1.0 if fused.direction == "call" else -1.0
                move = sign * (db[i + HORIZON].close - entry) / entry * 100.0
                rows.append({"tk": tk, "day": day, "window": pb.window,
                             "score": score, "move": move})

    days = sorted({r["day"] for r in rows})
    cut = days[len(days) // 2]
    early = [r["move"] for r in rows if r["day"] < cut]
    late = [r["move"] for r in rows if r["day"] >= cut]
    allm = [r["move"] for r in rows]
    out = {
        "label": LABEL, "n": len(rows), "sessions": len(days),
        "mean": mean(allm) if allm else 0.0,
        "hit": (sum(1 for m in allm if m > 0) / len(allm) * 100.0) if allm else 0.0,
        "early": mean(early) if early else 0.0,
        "late": mean(late) if late else 0.0,
    }
    print(f"[{LABEL}] {out['n']} signals over {out['sessions']} sessions")
    print(f"  mean signed move at {HORIZON}m : {out['mean']:+.4f}%")
    print(f"  direction correct          : {out['hit']:.1f}%")
    print(f"  early half / late half     : {out['early']:+.4f}% / {out['late']:+.4f}%")
    (HIST / f"ab_{LABEL}.json").write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    main()
