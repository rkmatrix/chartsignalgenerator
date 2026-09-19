"""Pull as much 1m history as yfinance will serve (~30 days, in <=7 day chunks).

Usage: python scripts/fetch_1m_history.py [TICKER] [days]
Writes data/history/<TICKER>_1m_hist.json in the same shape as the desk cache.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT = Path(r"C:\Projects\trading\PA\data\history")

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 29
CHUNK = 7

symbol = {"SPX": "^GSPC"}.get(TICKER, TICKER)
end = datetime.now(ET).date() + timedelta(days=1)
start = end - timedelta(days=DAYS)
print(f"{TICKER} ({symbol})  {start} -> {end}")

rows: list[dict] = []
seen: set[str] = set()

# Keep whatever is already on disk. yfinance only serves a rolling ~30 days, so
# overwriting slides the window forward and throws away sessions we can never
# fetch again — the sample has to accumulate to be worth anything.
existing = OUT / f"{TICKER}_1m_hist.json"
if existing.exists():
    try:
        for b in json.loads(existing.read_text(encoding="utf-8")).get("bars") or []:
            if b.get("ts") and b["ts"] not in seen:
                seen.add(b["ts"])
                rows.append(b)
        print(f"  kept {len(rows)} bars already on disk")
    except Exception as exc:
        print(f"  could not read existing history ({exc}), starting fresh")

cursor = start
while cursor < end:
    stop = min(cursor + timedelta(days=CHUNK), end)
    try:
        frame = yf.download(
            symbol,
            start=cursor.isoformat(),
            end=stop.isoformat(),
            interval="1m",
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {cursor} -> {stop}: FAILED {exc}")
        cursor = stop
        continue
    if frame is None or frame.empty:
        print(f"  {cursor} -> {stop}: empty")
        cursor = stop
        continue
    if getattr(frame.columns, "nlevels", 1) > 1:
        frame.columns = frame.columns.get_level_values(0)
    added = 0
    for idx, row in frame.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        key = ts.isoformat()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "ts": key,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume") or 0),
            }
        )
        added += 1
    print(f"  {cursor} -> {stop}: {added} bars")
    cursor = stop

rows.sort(key=lambda r: r["ts"])
days = sorted({datetime.fromisoformat(r["ts"]).astimezone(ET).date().isoformat() for r in rows})
print(f"\ntotal {len(rows)} bars across {len(days)} sessions")
print("sessions:", days)

path = OUT / f"{TICKER}_1m_hist.json"
path.write_text(json.dumps({"bars": rows, "prior_close": None}), encoding="utf-8")
print("wrote", path)
