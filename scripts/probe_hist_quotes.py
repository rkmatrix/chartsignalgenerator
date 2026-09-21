"""Does Unusual Whales serve historical option quotes (bid/ask), not just trades?

The cost case rests on 65 trades and 2-6 samples per ticker, which is too thin
to act on. If historical NBBO is available the spread can be measured properly
across every name and session; if not, the fallback is to record spreads live
from here on, which works but only starts producing data today.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BASE = "https://api.unusualwhales.com"


def key() -> str:
    k = os.environ.get("UNUSUAL_WHALES_API_KEY", "")
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("UNUSUAL_WHALES_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def get(path: str, api_key: str) -> tuple[int, object]:
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")[:200]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)[:200]


def main() -> None:
    api = key()
    if not api:
        print("no API key found")
        return

    past = (date.today() - timedelta(days=5)).isoformat()
    tk = "SPY"

    # Find a real contract id to ask about.
    status, chain = get(f"/api/stock/{tk}/option-chains", api)
    contract = None
    if status == 200 and isinstance(chain, dict):
        data = chain.get("data") or []
        if data:
            first = data[0]
            contract = first if isinstance(first, str) else (first.get("option_symbol") or first.get("id"))
    print(f"chain status={status}  sample contract={contract}\n")

    candidates = [
        ("option-contract historic", f"/api/option-contract/{contract}/historic" if contract else None),
        ("option-contract intraday", f"/api/option-contract/{contract}/intraday" if contract else None),
        ("option-contract flow", f"/api/option-contract/{contract}/flow?limit=3" if contract else None),
        ("option-contract volume-profile",
         f"/api/option-contract/{contract}/volume-profile" if contract else None),
        ("stock option-trades dated",
         f"/api/stock/{tk}/option-trades?limit=3&date={past}"),
        ("stock option-chains dated", f"/api/stock/{tk}/option-chains?date={past}"),
        ("stock nope", f"/api/stock/{tk}/nope?date={past}"),
        ("stock ohlc 1m", f"/api/stock/{tk}/ohlc/1m?date={past}&limit=3"),
        ("stock quote", f"/api/stock/{tk}/stock-state"),
        ("historical risk reversal",
         f"/api/stock/{tk}/historical-risk-reversal-skew?date={past}"),
    ]

    print(f"{'endpoint':<34} {'status':>6}  has bid/ask?")
    for label, path in candidates:
        if not path:
            print(f"  {label:<32} skipped (no contract)")
            continue
        status, body = get(path, api)
        blob = json.dumps(body)[:4000].lower() if not isinstance(body, str) else body.lower()
        has = "YES" if ('"bid"' in blob or '"ask"' in blob or "nbbo" in blob) else "no"
        n = ""
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            n = f" n={len(body['data'])}"
        print(f"  {label:<32} {status:>6}  {has}{n}")
        if has == "YES" and isinstance(body, dict):
            data = body.get("data") or []
            if data and isinstance(data[0], dict):
                keys = sorted(data[0].keys())
                print(f"      fields: {', '.join(keys[:14])}")


if __name__ == "__main__":
    main()
