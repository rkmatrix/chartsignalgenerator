"""Did the option stop fire while the chart stop was still intact?

QQQ on 2026-09-24 fell 25% in the option while the stock moved 0.08% and
never reached the setup's own stop at 734.63. If that is the usual hard
stop, the premium limit is closing trades the chart has not invalidated.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"
ET = ZoneInfo("America/New_York")


def series(ticker: str, day: str) -> list[tuple[datetime, float]]:
    for name in (f"{ticker}_1m_hist.json", f"{ticker}_1m_today.json"):
        p = H / name
        if not p.exists():
            continue
        out = []
        for b in json.loads(p.read_text(encoding="utf-8")).get("bars") or []:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            if ts.strftime("%Y-%m-%d") == day and ts.strftime("%H:%M") >= "09:30":
                out.append((ts, float(b["close"])))
        if out:
            return out
    return []


def price_at(bars, ts: datetime, plus_min: int = 0) -> float | None:
    want = ts.timestamp() + plus_min * 60
    later = [px for t, px in bars if t.timestamp() >= want]
    return later[0] if later else None


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    rows = []
    for t in book["trades"]:
        if t.get("status") != "closed" or t.get("quote_source") != "uw":
            continue
        if t.get("pnl_pct") is None or not t.get("opened_at"):
            continue
        rows.append(t)

    print(f"{len(rows)} live-feed closes\n")
    print(f"{'day':<12} {'tk':<6} {'why':<16} {'opt%':>7} {'spot move':>10} "
          f"{'to stop':>8} {'intact':>7} {'+30m':>8}")

    recovered = 0
    intact_loss = 0.0
    intact_n = 0
    broken_n = 0
    for t in sorted(rows, key=lambda r: str(r.get("opened_at"))):
        reason = str(t.get("reason") or "")
        if reason not in {"hard_stop", "underlying_stop", "thesis_broken", "failed_breakout"}:
            continue
        opened = datetime.fromisoformat(t["opened_at"])
        closed = datetime.fromisoformat(t["closed_at"]) if t.get("closed_at") else opened
        bars = series(str(t["ticker"]), opened.strftime("%Y-%m-%d"))
        if not bars:
            continue
        entry_px = price_at(bars, opened)
        exit_px = price_at(bars, closed)
        later = price_at(bars, closed, 30)
        if entry_px is None or exit_px is None:
            continue
        sign = 1.0 if str(t.get("direction")) == "call" else -1.0
        spot_mv = (exit_px - entry_px) / entry_px * 100.0 * sign
        fwd = None if later is None else (later - exit_px) / exit_px * 100.0 * sign
        stop = t.get("stop")
        intact = None
        room = None
        if stop:
            stop = float(stop)
            if str(t.get("direction")) == "call":
                intact = exit_px >= stop
                room = (entry_px - stop) / entry_px * 100.0
            else:
                intact = exit_px <= stop
                room = (stop - entry_px) / entry_px * 100.0
        if reason == "hard_stop" and intact:
            intact_n += 1
            intact_loss += float(t["pnl_pct"]) / 100.0 * float(t["entry"]) * 100.0
            if fwd is not None and fwd > 0:
                recovered += 1
        if reason == "hard_stop" and intact is False:
            broken_n += 1
        flag = "" if intact is None else ("yes" if intact else "NO")
        fwd_s = "" if fwd is None else f"{fwd:+.2f}%"
        room_s = "" if room is None else f"{room:.2f}%"
        if opened.strftime("%Y-%m-%d") >= "2026-09-21" or reason == "hard_stop":
            print(f"{opened.strftime('%Y-%m-%d'):<12} {t['ticker']:<6} {reason:<16} "
                  f"{float(t['pnl_pct']):>+6.1f}% {spot_mv:>+9.2f}% {room_s:>8} {flag:>7} {fwd_s:>8}")

    print(f"\nhard stops with the chart stop still intact: {intact_n}, "
          f"option dollars {intact_loss:+.0f}, underlying green 30m later {recovered}")
    print(f"hard stops after the chart stop had already broken: {broken_n}")


if __name__ == "__main__":
    main()
