"""What does the Unusual Whales key actually give us, and is any of it historical?

Options flow is the only genuinely new information available to the desk: every
indicator it currently reads is a transform of the same 1m price series, which
is why adding MACD, RSI and Stochastic each produced edges far too small to pay
the round trip. Flow describes what positioned money is doing instead.

The whole plan depends on one thing though. A backtest needs history, and if the
endpoints only serve "right now" then nothing can be validated without first
collecting forward for weeks. So this probes availability AND whether a date
parameter is honoured, before any analysis is built on top.

Prints no key material.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, r"C:\Projects\trading\PA\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.config import get_settings  # noqa: E402

TOKEN = (get_settings().unusual_whales_api_key or "").strip()
BASE = "https://api.unusualwhales.com"
TICKER = "SPY"

# A weekday comfortably inside the window the desk has bars for.
past = date(2026, 9, 10)

CANDIDATES = [
    ("flow alerts", f"/api/stock/{TICKER}/flow-alerts?limit=5"),
    ("option trades", f"/api/stock/{TICKER}/option-trades?limit=5"),
    ("flow per expiry", f"/api/stock/{TICKER}/flow-per-expiry"),
    ("net prem ticks", f"/api/stock/{TICKER}/net-prem-ticks"),
    ("net prem ticks DATED", f"/api/stock/{TICKER}/net-prem-ticks?date={past}"),
    ("options volume", f"/api/stock/{TICKER}/options-volume"),
    ("options volume DATED", f"/api/stock/{TICKER}/options-volume?limit=5&date={past}"),
    ("greek exposure", f"/api/stock/{TICKER}/greek-exposure"),
    ("greek exposure DATED", f"/api/stock/{TICKER}/greek-exposure?date={past}"),
    ("spot exposures", f"/api/stock/{TICKER}/spot-exposures"),
    ("darkpool", f"/api/darkpool/{TICKER}?limit=5"),
    ("darkpool DATED", f"/api/darkpool/{TICKER}?limit=5&date={past}"),
    ("oi change", f"/api/stock/{TICKER}/oi-change?limit=5"),
    ("stock state", f"/api/stock/{TICKER}/stock-state"),
    ("etf tide", f"/api/market/{TICKER}/etf-tide"),
    ("market tide", "/api/market/market-tide"),
    ("market tide DATED", f"/api/market/market-tide?date={past}"),
]


def probe(path: str) -> tuple[int, object]:
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.reason
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def describe(payload: object) -> str:
    if not isinstance(payload, dict):
        return f"{type(payload).__name__}"
    data = payload.get("data", payload)
    if isinstance(data, list):
        if not data:
            return "0 rows"
        keys = list(data[0])[:9] if isinstance(data[0], dict) else []
        return f"{len(data)} rows; fields: {', '.join(map(str, keys))}"
    if isinstance(data, dict):
        return f"object; fields: {', '.join(list(data)[:9])}"
    return str(data)[:70]


def main() -> None:
    if not TOKEN:
        print("no API key configured")
        return
    print(f"key present (length {len(TOKEN)}), probing {len(CANDIDATES)} endpoints\n")
    ok = []
    for label, path in CANDIDATES:
        status, payload = probe(path)
        if status == 200:
            ok.append(label)
            print(f"  [200] {label:<24} {describe(payload)}")
        else:
            print(f"  [{status or 'ERR'}] {label:<24} {str(payload)[:60]}")
    print(f"\n{len(ok)} of {len(CANDIDATES)} available")

    # The question that decides everything: does a dated request return data
    # from that date, or silently return today's?
    print("\n=== is dated data really historical? ===")
    for label, path in CANDIDATES:
        if "DATED" not in label:
            continue
        status, payload = probe(path)
        if status != 200:
            continue
        blob = json.dumps(payload)[:400]
        hit = str(past) in blob
        print(f"  {label:<26} mentions {past}: {'YES' if hit else 'no'}")
        print(f"    sample: {blob[:180]}")


if __name__ == "__main__":
    main()
