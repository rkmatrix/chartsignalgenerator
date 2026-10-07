"""Quote paths and the underlying move after each Sep 24 entry."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / "data" / "history"
ET = ZoneInfo("America/New_York")
DAY = "2026-09-24"


def bars(ticker: str) -> list[dict]:
    for name in (f"{ticker}_1m_hist.json", f"{ticker}_1m_today.json"):
        p = H / name
        if not p.exists():
            continue
        out = []
        for b in json.loads(p.read_text(encoding="utf-8")).get("bars") or []:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            if ts.strftime("%Y-%m-%d") == DAY and ts.strftime("%H:%M") >= "09:30":
                out.append({"t": ts.strftime("%H:%M"), "c": float(b["close"])})
        if out:
            return out
    return []


def fwd(ticker: str, opened: str, direction: str) -> str:
    series = bars(ticker)
    if not series:
        return "no bars"
    start = opened[11:16]
    sign = 1.0 if direction == "call" else -1.0
    at = [b for b in series if b["t"] >= start]
    if not at:
        return "no bars after entry"
    px = at[0]["c"]
    bits = []
    for mins in (5, 15, 30):
        hh, mm = int(start[:2]), int(start[3:])
        total = hh * 60 + mm + mins
        want = f"{total // 60:02d}:{total % 60:02d}"
        later = [b for b in at if b["t"] >= want]
        if later:
            mv = (later[0]["c"] - px) / px * 100.0 * sign
            bits.append(f"+{mins}m {mv:+.2f}%")
    return f"spot {px:.2f}  " + "  ".join(bits)


def main() -> None:
    book = json.loads((H / "signal_book.json").read_text(encoding="utf-8"))
    rows = [t for t in book["trades"] if str(t.get("opened_at") or "")[:10] == DAY]
    for t in sorted(rows, key=lambda r: str(r.get("opened_at"))):
        entry = float(t["entry"])
        print(f"=== {t['ticker']} {t['direction']} {t.get('reason')} ===")
        print(f"  {fwd(t['ticker'], t['opened_at'], t['direction'])}")
        print(f"  stop={t.get('stop')} trigger={t.get('trigger')}")
        for m in t.get("marks") or []:
            if not (isinstance(m, (list, tuple)) and len(m) >= 2):
                continue
            bid = float(m[2]) if len(m) > 2 and m[2] is not None else float(m[1])
            pc = (bid - entry) / entry * 100.0
            print(f"    {m[0]}  mark={m[1]} bid={m[2] if len(m) > 2 else None}  sellable {pc:+.1f}%")
        print()


if __name__ == "__main__":
    main()
