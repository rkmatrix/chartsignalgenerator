"""Did Unusual Whales options flow agree with the AlphaWave trades that worked?

For each closed AlphaWave trade, net delta and net premium (calls minus puts)
are summed over the minutes before entry from UW net-prem-ticks for that day.
"Agrees" means the flow points the same way as the trade.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pa.config import get_settings  # noqa: E402
from wave_learn import _bucket_time, _wave_trades  # noqa: E402

CACHE = ROOT / "data" / "history" / "uw_ticks"
URL = "https://api.unusualwhales.com/api/stock/{t}/net-prem-ticks?date={d}"


def ticks(ticker: str, day: str) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{ticker}_{day}.json"
    if path.exists():
        return json.loads(path.read_text())
    key = get_settings().unusual_whales_api_key.strip()
    req = urllib.request.Request(URL.format(t=ticker, d=day), headers={"Authorization": f"Bearer {key}"})
    rows = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("data") or []
    path.write_text(json.dumps(rows))
    return rows


def flow_before(ticker: str, opened: datetime, minutes: int) -> tuple[float, float]:
    day = opened.strftime("%Y-%m-%d")
    end = opened.astimezone(tz=None).timestamp()
    start = end - minutes * 60
    delta = prem = 0.0
    for row in ticks(ticker, day):
        ts = datetime.fromisoformat(row["tape_time"].replace("Z", "+00:00")).timestamp()
        if start <= ts < end:
            delta += float(row.get("net_delta") or 0)
            prem += float(row.get("net_call_premium") or 0) - float(row.get("net_put_premium") or 0)
    return delta, prem


def main() -> None:
    rows = _wave_trades()
    for minutes in (15, 30, 9999):
        stats: dict[str, list[float]] = defaultdict(list)
        days: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for t in rows:
            opened = datetime.fromisoformat(t["opened_at"])
            delta, prem = flow_before(t["ticker"], opened, minutes)
            sign = 1 if t["direction"] == "call" else -1
            for name, val in (("delta", delta), ("prem", prem)):
                tag = f"{name} agrees" if val * sign > 0 else f"{name} disagrees"
                stats[tag].append(float(t["pnl_dollars"]))
                days[tag][opened.strftime("%m-%d")] += float(t["pnl_dollars"])
                if not _bucket_time(t).startswith("a"):
                    key = tag + " (after 09:45)"
                    stats[key].append(float(t["pnl_dollars"]))
                    days[key][opened.strftime("%m-%d")] += float(t["pnl_dollars"])
        label = "whole day" if minutes == 9999 else f"last {minutes}m"
        print(f"\n== flow over {label} before entry")
        for tag in sorted(stats):
            vals = stats[tag]
            win = sum(v > 0 for v in vals) / len(vals) * 100
            per_day = " ".join(f"{d}:{v:+.0f}" for d, v in sorted(days[tag].items()))
            print(f"{tag:<32} n={len(vals):>3} win={win:3.0f}% ${sum(vals):+6.0f} | {per_day}")


if __name__ == "__main__":
    main()
