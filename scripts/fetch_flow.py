"""Pull historical per-minute options flow from Unusual Whales, one file per ticker.

Every indicator the desk reads is a transform of the same 1m price series, which
is why MACD, RSI and Stochastic each produced edges far below the round-trip
cost: they cannot disagree with price in any useful way. Flow is different
information — it is what positioned money is doing, not another view of the
tape.

net-prem-ticks serves a full past session at minute granularity (405 rows from
13:30Z to 20:14Z), so this can be joined straight onto the bars the backtests
already use and validated on the same 25 sessions.

Fields kept, and why:
  net_call_volume   call contracts hit on the ask less those hit on the bid;
                    positive means calls are being bought aggressively
  net_put_volume    the same for puts
  net_call_premium  the dollar version, which weights size rather than count
  net_put_premium
  net_delta         directional exposure the flow is adding, the most direct
                    statement of which way that money is leaning

Usage: python scripts/fetch_flow.py [TICKER ...]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, r"C:\Projects\trading\PA\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.config import get_settings  # noqa: E402

ET = ZoneInfo("America/New_York")
HIST = Path(r"C:\Projects\trading\PA\data\history")
OUT = HIST / "flow"
TOKEN = (get_settings().unusual_whales_api_key or "").strip()
PAUSE = 0.35  # be a polite client; this is several hundred requests
KEEP = (
    "net_call_volume",
    "net_put_volume",
    "net_call_premium",
    "net_put_premium",
    "net_delta",
)


def sessions_for(tk: str) -> list[str]:
    path = HIST / f"{tk}_1m_hist.json"
    if not path.exists():
        return []
    days = set()
    for b in json.loads(path.read_text(encoding="utf-8"))["bars"]:
        days.add(datetime.fromisoformat(b["ts"]).astimezone(ET).date().isoformat())
    return sorted(days)


def fetch(tk: str, day: str) -> dict[str, dict] | None:
    url = (
        f"https://api.unusualwhales.com/api/stock/{tk}/net-prem-ticks?date={day}"
    )
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            rows = json.loads(resp.read().decode("utf-8")).get("data") or []
    except urllib.error.HTTPError as exc:
        if exc.code == 429:  # rate limited; back off and let the caller retry
            time.sleep(5.0)
        return None
    except Exception:  # noqa: BLE001
        return None

    out: dict[str, dict] = {}
    for r in rows:
        stamp = r.get("tape_time")
        if not stamp:
            continue
        try:
            ts = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(ET)
        except ValueError:
            continue
        if ts.date().isoformat() != day:
            continue
        rec = {}
        for k in KEEP:
            v = r.get(k)
            if v is None:
                continue
            try:
                rec[k] = float(v)
            except (TypeError, ValueError):
                continue
        if rec:
            out[ts.strftime("%Y-%m-%d %H:%M")] = rec
    return out


def main() -> None:
    if not TOKEN:
        print("no API key configured")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = [t.upper() for t in sys.argv[1:]] or sorted(
        {p.name.split("_")[0].upper() for p in HIST.glob("*_1m_hist.json")}
    )
    for tk in wanted:
        dest = OUT / f"{tk}_flow.json"
        have: dict[str, dict] = {}
        if dest.exists():
            try:
                have = json.loads(dest.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                have = {}
        days = sessions_for(tk)
        todo = [d for d in days if d not in have]
        if not todo:
            print(f"  {tk:<6} {len(have)} sessions already cached")
            continue
        got = 0
        for day in todo:
            rows = fetch(tk, day)
            if rows is None:  # one retry after a backoff
                rows = fetch(tk, day)
            if rows:
                have[day] = rows
                got += 1
            time.sleep(PAUSE)
        dest.write_text(json.dumps(have), encoding="utf-8")
        minutes = sum(len(v) for v in have.values())
        print(f"  {tk:<6} +{got} sessions -> {len(have)} total, {minutes} minutes")


if __name__ == "__main__":
    main()
