"""Replay a session minute by minute and show where each signal died.

META ran +9.4% today and the desk booked one entry, at 14:34, near the top,
for -27.7%. Two very different explanations fit that: the engine never saw the
trend, or it saw it repeatedly and something downstream refused it. They call
for opposite fixes, so this walks the real bars through the real pipeline and
counts the drops at every stage.

Usage: python scripts/replay_today.py META [SPY AMZN ...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for, verdict_for  # noqa: E402
from pa.open_session.playbook import apply_playbook, session_window  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402

ET = ZoneInfo("America/New_York")
H = ROOT / "data" / "history"
COOLDOWN = 15


def load_today(ticker: str) -> list[Bar]:
    for name in (f"{ticker}_1m_today.json", f"{ticker}_1m_hist.json"):
        p = H / name
        if not p.exists():
            continue
        today = datetime.now(ET).strftime("%Y-%m-%d")
        out = []
        for b in json.loads(p.read_text(encoding="utf-8")).get("bars") or []:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            if ts.strftime("%Y-%m-%d") != today:
                continue
            out.append(
                Bar(
                    ticker=ticker, timeframe=Timeframe.M1, ts=ts,
                    open=float(b.get("open") or b["close"]),
                    high=float(b.get("high") or b["close"]),
                    low=float(b.get("low") or b["close"]),
                    close=float(b["close"]),
                    volume=float(b.get("volume") or 0.0),
                )
            )
        if out:
            return sorted(out, key=lambda x: x.ts)
    return []


def main() -> None:
    tickers = sys.argv[1:] or ["META"]
    for ticker in tickers:
        bars = load_today(ticker)
        rth = [b for b in bars if b.ts.strftime("%H:%M") >= "09:30"]
        if len(rth) < 40:
            print(f"{ticker}: only {len(rth)} RTH bars, skipping\n")
            continue

        print(f"=== {ticker}: {len(rth)} RTH bars, "
              f"{rth[0].ts.strftime('%H:%M')} to {rth[-1].ts.strftime('%H:%M')} ===")
        open_px = rth[0].close
        drops = Counter()
        printed = []
        last_fire = {}

        for i in range(30, len(rth)):
            upto = rth[: i + 1]
            now = upto[-1].ts
            elapsed = (now - now.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60.0
            window = session_window(elapsed) or ""
            px = upto[-1].close

            cands = setups_for(ticker, upto, orb_minutes=15)
            if not cands:
                drops["no setup fired"] += 1
                continue

            kept, _pb = apply_playbook(cands, ticker, elapsed)
            if not kept:
                drops[f"playbook blocked ({window})"] += 1
                continue

            fused = fuse(kept)
            if not fused:
                drops["fuse: no confluence"] += 1
                continue
            if fused.vetoed:
                drops[f"fuse veto: {fused.veto_reason}"] += 1
                continue

            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            score = score_for(fused.conviction, families=n_fam, solo=solo)
            verdict = verdict_for(score)
            if verdict == "SKIP":
                drops[f"score {score} below cut"] += 1
                continue

            key = (ticker, fused.direction)
            prev = last_fire.get(key)
            if prev is not None and (now - prev).total_seconds() / 60.0 < COOLDOWN:
                drops["cooldown"] += 1
                continue
            last_fire[key] = now

            printed.append({
                "t": now.strftime("%H:%M"), "dir": fused.direction,
                "score": score, "window": window,
                "stack": "+".join(sorted(fused.strategies or [])),
                "from_open": (px - open_px) / open_px * 100.0,
            })

        print(f"  {len(printed)} signals would print; drops below\n")
        for reason, n in drops.most_common(8):
            print(f"    {n:>4}  {reason}")

        print(f"\n  signals ({len(printed)}):")
        for s in printed:
            print(f"    {s['t']}  {s['dir']:<4} score={s['score']:<3} {s['window']:<12}"
                  f" from_open={s['from_open']:+6.2f}%  {s['stack']}")

        calls = [s for s in printed if s["dir"] == "call"]
        if calls:
            print(f"\n  first call at {calls[0]['t']} "
                  f"(price was {calls[0]['from_open']:+.2f}% from open)")
            print(f"  last  call at {calls[-1]['t']} "
                  f"({calls[-1]['from_open']:+.2f}% from open)")
        print()


if __name__ == "__main__":
    main()
