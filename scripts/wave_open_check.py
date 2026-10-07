"""Which 5m bar fired the 09:30 AlphaWave prints, and with what volume."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.open_session.alphawave import evaluate, resample_5m
from pa.open_session.bars import _rows_to_bars

ET = ZoneInfo("America/New_York")
H = Path(__file__).resolve().parents[1] / "data" / "history"


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-30"
    for tk, at in (("MSFT", "09:30:06"), ("AMZN", "09:30:06"), ("NFLX", "09:30:06"),
                   ("AVGO", "09:30:06"), ("GOOGL", "09:35:21"), ("META", "09:55:25"),
                   ("JPM", "09:47:10"), ("SPY", "09:55:25")):
        p = H / f"{tk}_1m_today.json"
        if not p.exists():
            print(tk, "no bars")
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))["bars"]
        bars = _rows_to_bars(tk, rows)
        now = datetime.fromisoformat(f"{day}T{at}").replace(tzinfo=ET)
        upto = [b for b in bars if b.ts <= now]
        five = resample_5m(upto, now)
        res = evaluate(upto, now)
        tail = five[-4:]
        print(tk, at, "event", None if res.event is None else (res.event.kind, res.event.engine, str(res.event.bar_ts)[11:16]))
        for b in tail:
            print("   ", str(b.ts)[5:16], f"c={b.close:.2f} v={b.volume:,.0f}")
        rth = [b for b in five if b.ts.strftime("%H:%M") >= "09:30" and b.ts.date() == now.date()]
        print("    rth 5m bars closed:", len(rth))


if __name__ == "__main__":
    main()
