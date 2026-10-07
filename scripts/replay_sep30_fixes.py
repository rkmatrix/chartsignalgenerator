"""Replay a session's booked AlphaWave trades under the fixed rules.

Signal: the indicator re-run on regular-session bars at the moment of entry.
Spread: first bid in the trade's own path, against the fill.
Re-entry: a name with a losing close earlier in the day is skipped.
Daily stop: realised plus open-at-bid, approximated with closed results.

Signals the fixed indicator would print that the old one did not are
listed but cannot be priced: there is no option quote for a trade that
never opened.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.open_session.alphawave import evaluate
from pa.open_session.bars import _rows_to_bars
from pa.open_session.ledger import MAX_DAILY_LOSS, MAX_ENTRY_SPREAD_PCT

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"


def bars_for(tk: str):
    p = H / f"{tk}_1m_today.json"
    if not p.exists():
        return []
    return _rows_to_bars(tk, json.loads(p.read_text(encoding="utf-8"))["bars"])


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-30"
    trades = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))["trades"]
    rows = sorted(
        [t for t in trades if str(t.get("opened_at") or "").startswith(day)],
        key=lambda t: str(t["opened_at"]),
    )
    cache: dict = {}
    kept_pnl = 0.0
    lost_names: set[str] = set()
    closed: list[tuple[datetime, float]] = []
    for t in rows:
        tk = t["ticker"]
        opened = datetime.fromisoformat(t["opened_at"])
        bars = cache.setdefault(tk, bars_for(tk))
        upto = [b for b in bars if b.ts <= opened]
        res = evaluate(upto, opened)
        want = "CALL" if t["direction"] == "call" else "PUT"
        fired = res.event is not None and res.event.kind == want
        entry = float(t.get("entry") or 0)
        first_bid = next(
            (float(m[2]) for m in (t.get("marks") or []) if isinstance(m, list) and len(m) >= 3 and m[2]),
            None,
        )
        gap = None if not (entry and first_bid) else (entry - first_bid) / entry * 100
        realised = sum(p for c, p in closed if c <= opened)
        why = ""
        if not fired:
            why = "no RTH signal"
        elif gap is not None and gap >= MAX_ENTRY_SPREAD_PCT:
            why = f"spread {gap:.0f}%"
        elif tk in lost_names:
            why = "lost today"
        elif realised <= -MAX_DAILY_LOSS:
            why = "daily stop"
        pnl = float(t.get("pnl_dollars") or 0)
        if not why:
            kept_pnl += pnl
            if t.get("closed_at"):
                closed.append((datetime.fromisoformat(t["closed_at"]), pnl))
                if pnl < 0:
                    lost_names.add(tk)
        print(f"{str(t['opened_at'])[11:19]} {tk:<5} {t['direction']:<4} ${pnl:+5.0f}  "
              f"{'KEEP' if not why else 'skip: ' + why}")
    print(f"\nbooked ${sum(float(t.get('pnl_dollars') or 0) for t in rows):+.0f}  "
          f"after fixes ${kept_pnl:+.0f}")

    print("\nRTH-only signals the old run did not book (unpriced):")
    booked = {(t["ticker"], t["direction"]) for t in rows}
    start = datetime.fromisoformat(f"{day}T09:35:00").replace(tzinfo=ET)
    end = datetime.fromisoformat(f"{day}T16:00:00").replace(tzinfo=ET)
    names = sorted({p.name.split("_")[0] for p in H.glob("*_1m_today.json")})
    for tk in names:
        bars = cache.setdefault(tk, bars_for(tk))
        if not bars:
            continue
        now = start
        while now <= end:
            res = evaluate([b for b in bars if b.ts < now], now)
            if res.event is not None and res.event.kind in {"CALL", "PUT"}:
                side = res.event.kind.lower()
                if (tk, side) not in booked:
                    print(f"  {now.strftime('%H:%M')} {tk:<5} {res.event.kind} {res.event.engine}")
            now += timedelta(minutes=5)


if __name__ == "__main__":
    main()
