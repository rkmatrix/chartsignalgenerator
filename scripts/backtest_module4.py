"""Module 4: do RSI, MACD or Stochastic add anything to the signals we emit?

The desk already reads trend (EMAs, VWAP) and volatility (Bollinger). It reads
no oscillator at all. The curriculum treats those as core, so test them the only
way worth testing: as a veto on signals the desk already produces, one at a
time, so a winner cannot hide behind the other two.

Each indicator is asked the same question — does it agree with the direction we
are about to take? Filters that help should raise the forward return of what
survives; a filter that only cuts the sample is worth nothing.

Usage: python scripts/backtest_module4.py [hold_bars] [take_cut]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for  # noqa: E402
from pa.open_session.playbook import apply_playbook  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
HOLD = int(sys.argv[1]) if len(sys.argv) > 1 else 15
TAKE_CUT = int(sys.argv[2]) if len(sys.argv) > 2 else 75
COOLDOWN = 15


def ema_last(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    k = 2.0 / (n + 1)
    cur = sum(vals[:n]) / n
    for v in vals[n:]:
        cur = v * k + cur * (1 - k)
    return cur


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(0.0, d)
        losses += max(0.0, -d)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def macd_hist(closes: list[float]) -> float | None:
    """MACD line minus its signal. Signal needs a history of the line itself."""
    if len(closes) < 35:
        return None
    line = []
    for end in range(26, len(closes) + 1):
        f = ema_last(closes[:end], 12)
        s = ema_last(closes[:end], 26)
        if f is None or s is None:
            return None
        line.append(f - s)
    if len(line) < 9:
        return None
    return line[-1] - (ema_last(line, 9) or 0.0)


def stoch(bars: list[Bar], n: int = 14, smooth: int = 3) -> tuple[float, float] | None:
    if len(bars) < n + smooth:
        return None
    ks = []
    for end in range(len(bars) - smooth, len(bars)):
        win = bars[end - n + 1:end + 1]
        hi = max(b.high for b in win)
        lo = min(b.low for b in win)
        if hi == lo:
            return None
        ks.append((win[-1].close - lo) / (hi - lo) * 100.0)
    return ks[-1], sum(ks) / len(ks)


hist_dir = Path(r"C:\Projects\trading\PA\data\history")
signals: list[dict] = []

for path in sorted(hist_dir.glob("*_1m_hist.json")):
    TICKER = path.name.split("_")[0].upper()
    payload = json.loads(path.read_text(encoding="utf-8"))
    bars = [
        Bar(ticker=TICKER, ts=datetime.fromisoformat(b["ts"]),
            open=float(b["open"]), high=float(b["high"]), low=float(b["low"]),
            close=float(b["close"]), volume=float(b["volume"]), timeframe=Timeframe.M1)
        for b in payload["bars"]
    ]
    by_day: dict[str, list[Bar]] = {}
    for bar in bars:
        by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)
    days = [d for d in sorted(by_day)
            if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300]

    for di, day in enumerate(days):
        day_bars = by_day[day]
        prior_close = None if di == 0 else by_day[days[di - 1]][-1].close
        rth_idx = [i for i, b in enumerate(day_bars)
                   if b.ts.astimezone(ET).time() >= RTH_OPEN]
        last_sig = -10_000
        for i in rth_idx:
            clock = day_bars[i].ts.astimezone(ET)
            elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
            if elapsed < 0 or i - last_sig < COOLDOWN:
                continue
            cands = setups_for(TICKER, day_bars[: i + 1], orb_minutes=15, prior_close=prior_close)
            if not cands:
                continue
            cands, pb = apply_playbook(cands, TICKER, elapsed)
            fused = fuse(cands, spy_bias=None)
            if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
                continue
            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            score = score_for(fused.conviction, families=n_fam, solo=solo)
            if pb.window == "first_hour" or score < TAKE_CUT:
                continue
            last_sig = i

            window = day_bars[max(0, i - 60): i + 1]
            closes = [b.close for b in window]
            entry = day_bars[i].close
            j = min(i + HOLD, len(day_bars) - 1)
            move = (day_bars[j].close - entry) / entry * 100.0
            call = fused.direction == "call"
            st = stoch(window)
            signals.append({
                "day": day, "ticker": TICKER, "call": call,
                "move": move if call else -move,
                "rsi": rsi(closes), "macd": macd_hist(closes),
                "k": st[0] if st else None, "d": st[1] if st else None,
            })

print(f"{len(signals)} TAKE-grade signals across "
      f"{len({s['ticker'] for s in signals})} tickers, "
      f"{len({s['day'] for s in signals})} sessions\n")


def show(label: str, rs: list[dict]) -> None:
    if not rs:
        print(f"  {label:<34} n=0")
        return
    win = sum(1 for r in rs if r["move"] > 0)
    avg = sum(r["move"] for r in rs) / len(rs)
    print(f"  {label:<34} n={len(rs):<5} win={win / len(rs) * 100:>4.1f}%  "
          f"avg={avg:>+7.4f}%  total={sum(r['move'] for r in rs):>+8.2f}%")


def agrees(r: dict, which: str) -> bool | None:
    if which == "rsi":
        if r["rsi"] is None:
            return None
        return r["rsi"] > 50 if r["call"] else r["rsi"] < 50
    if which == "rsi_extreme":
        if r["rsi"] is None:
            return None
        # Do not buy what is already overbought, or sell what is oversold.
        return r["rsi"] < 70 if r["call"] else r["rsi"] > 30
    if which == "macd":
        if r["macd"] is None:
            return None
        return r["macd"] > 0 if r["call"] else r["macd"] < 0
    if which == "stoch":
        if r["k"] is None or r["d"] is None:
            return None
        return r["k"] > r["d"] if r["call"] else r["k"] < r["d"]
    return None


print("=== baseline, then each filter alone ===")
show("no filter", signals)
for which in ("rsi", "rsi_extreme", "macd", "stoch"):
    show(f"require {which} agrees", [s for s in signals if agrees(s, which)])
    show(f"  (what it rejected)", [s for s in signals if agrees(s, which) is False])

print("\n=== out of sample, split by date ===")
days_all = sorted({s["day"] for s in signals})
mid = days_all[len(days_all) // 2]
for which in ("rsi", "rsi_extreme", "macd", "stoch"):
    for half, sel in (("early", [s for s in signals if s["day"] < mid]),
                      ("late", [s for s in signals if s["day"] >= mid])):
        base = sum(s["move"] for s in sel) / max(1, len(sel))
        keep = [s for s in sel if agrees(s, which)]
        got = sum(s["move"] for s in keep) / max(1, len(keep))
        print(f"  {which:<12} {half:<6} avg {base:>+7.4f}% -> {got:>+7.4f}% "
              f"(n={len(keep)})  {'better' if got > base else 'worse'}")
