"""What separates AlphaWave PASS from FAIL, judged on the recorded UW quote paths.

Every closed AlphaWave trade with Unusual Whales marks is grouped by entry
time, premium, entry spread and verdict, then replayed under alternative exit
rules using its own bid path. Results are split by session so a rule that only
fixes one day is visible as such.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "data" / "history" / "signal_book.json"


def _wave_trades() -> list[dict]:
    trades = json.loads(BOOK.read_text(encoding="utf-8"))["trades"]
    out = []
    for t in trades:
        if t.get("status") != "closed" or "alphawave" not in (t.get("families") or []):
            continue
        if t.get("quote_source") != "uw" or not t.get("marks") or not t.get("entry"):
            continue
        out.append(t)
    return out


def _bucket_time(t: dict) -> str:
    hhmm = str(t.get("opened_at"))[11:16]
    if hhmm < "09:45":
        return "a 09:30-09:45"
    if hhmm < "10:30":
        return "b 09:45-10:30"
    if hhmm < "12:00":
        return "c 10:30-12:00"
    if hhmm < "14:00":
        return "d 12:00-14:00"
    return "e 14:00-close"


def _spread(t: dict) -> float | None:
    bid, ask = t.get("entry_bid"), t.get("entry_ask")
    if not bid or not ask:
        return None
    mid = (float(bid) + float(ask)) / 2
    return (float(ask) - float(bid)) / mid * 100 if mid else None


def _replay(t: dict, *, tp: float | None, stop: float, arm: float, keep: float) -> float:
    """Return % P&L exiting on the bid under a simple rule, else the real exit."""
    entry = float(t["entry"])
    peak = entry
    for _ts, _mark, bid in t["marks"]:
        bid = float(bid or 0)
        if bid <= 0:
            continue
        peak = max(peak, bid)
        if tp is not None and bid >= entry * (1 + tp / 100):
            return (bid - entry) / entry * 100
        run = (peak - entry) / entry * 100
        if run >= arm and bid <= entry + (peak - entry) * keep:
            return (bid - entry) / entry * 100
        if bid <= entry * (1 - stop / 100):
            return (bid - entry) / entry * 100
    return float(t.get("pnl_pct") or 0)


def _summ(label: str, rows: list[dict], pnl) -> None:
    by_day: dict[str, float] = defaultdict(float)
    total = 0.0
    wins = 0
    for t in rows:
        dollars = pnl(t) / 100 * float(t["entry"]) * 100 * int(t.get("contracts") or 1)
        by_day[str(t["opened_at"])[:10]] += dollars
        total += dollars
        wins += pnl(t) > 0
    days = " ".join(f"{d[5:]}:{v:+.0f}" for d, v in sorted(by_day.items()))
    wr = wins / len(rows) * 100 if rows else 0
    print(f"{label:<34} n={len(rows):>3} win={wr:4.0f}% ${total:+7.0f} | {days}")


def main() -> None:
    rows = _wave_trades()
    actual = lambda t: float(t.get("pnl_pct") or 0)  # noqa: E731
    print("== actual, by group")
    _summ("all", rows, actual)
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in rows:
        groups["time " + _bucket_time(t)].append(t)
        groups["verdict " + str(t.get("verdict"))].append(t)
        groups["engine " + "+".join(t.get("strategies") or [])].append(t)
        prem = float(t["entry"])
        groups["premium " + ("<1" if prem < 1 else "1-2" if prem < 2 else ">=2")].append(t)
        sp = _spread(t)
        groups["spread " + ("?" if sp is None else "<4%" if sp < 4 else "4-8%" if sp < 8 else ">=8%")].append(t)
        groups["exit " + str(t.get("reason"))].append(t)
    for key in sorted(groups):
        _summ(key, groups[key], actual)

    print("\n== peak bid reached (how much room winners had)")
    for t in rows:
        t["_peak"] = max((float(m[2] or 0) for m in t["marks"]), default=0)
    for cut in (5, 10, 15, 20, 30):
        hit = sum((t["_peak"] - float(t["entry"])) / float(t["entry"]) * 100 >= cut for t in rows)
        print(f"  peak bid >= +{cut}%: {hit}/{len(rows)}")

    print("\n== exit replays on the recorded bid path (all wave trades)")
    for tp in (None, 10, 15, 20, 30):
        for stop in (15, 20, 25):
            for arm, keep in ((10, 0.5), (8, 0.6), (15, 0.6)):
                label = f"tp={tp} stop={stop} arm={arm} keep={keep}"
                _summ(label, rows, lambda t, a=(tp, stop, arm, keep): _replay(t, tp=a[0], stop=a[1], arm=a[2], keep=a[3]))


if __name__ == "__main__":
    main()
