"""Pull historical NBBO spreads per ticker from Unusual Whales.

The cost case currently rests on 65 trades with 2-6 samples per name, which is
too thin to narrow a watchlist on. option-contract/{id}/historic returns daily
nbbo_bid / nbbo_ask per contract, so the spread can be measured across many
contracts and sessions instead.

One caveat worth stating: these are end-of-day snapshots, not intraday quotes.
Spreads typically widen into the close, so this likely overstates the cost a
little. It is used for RELATIVE comparison between tickers, which is the
decision at hand -- which names are cheap enough to trade -- and that ordering
is far more robust than the absolute level.

Samples contracts near the money with near-dated expiries, since those are what
the desk actually trades.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

H = ROOT / "data" / "history"
OUT = H / "spread_cache.json"
BASE = "https://api.unusualwhales.com"

CONTRACTS_PER_TICKER = 12
MAX_DTE = 45
PAUSE = 0.15


def api_key() -> str:
    k = os.environ.get("UNUSUAL_WHALES_API_KEY", "")
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("UNUSUAL_WHALES_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def get(path: str, key: str):
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            time.sleep(2.0)
        return None
    except Exception:
        return None


def parse_symbol(sym: str) -> tuple[str, date, str, float] | None:
    """OCC symbol -> (ticker, expiry, right, strike). e.g. SPY270115C00505000."""
    try:
        i = 0
        while i < len(sym) and not sym[i].isdigit():
            i += 1
        ticker, rest = sym[:i], sym[i:]
        expiry = datetime.strptime(rest[:6], "%y%m%d").date()
        right = rest[6]
        strike = int(rest[7:]) / 1000.0
        return ticker, expiry, right, strike
    except Exception:
        return None


def spot_for(ticker: str) -> float | None:
    p = H / f"{ticker}_1m_hist.json"
    if not p.exists():
        return None
    try:
        bars = json.loads(p.read_text(encoding="utf-8"))["bars"]
        return float(bars[-1]["close"]) if bars else None
    except Exception:
        return None


def main() -> None:
    key = api_key()
    if not key:
        print("no API key")
        return

    tickers = sorted(
        p.name.split("_")[0].upper() for p in H.glob("*_1m_hist.json")
    )
    today = date.today()
    results: dict[str, list[dict]] = defaultdict(list)

    for tk in tickers:
        spot = spot_for(tk)
        if not spot:
            print(f"{tk}: no spot, skipped")
            continue
        chain = get(f"/api/stock/{tk}/option-chains", key)
        syms = (chain or {}).get("data") or []
        parsed = []
        for s in syms:
            sym = s if isinstance(s, str) else (s.get("option_symbol") or "")
            got = parse_symbol(sym)
            if not got:
                continue
            _, expiry, right, strike = got
            dte = (expiry - today).days
            if dte < 0 or dte > MAX_DTE:
                continue
            parsed.append((abs(strike - spot) / spot, dte, sym))
        parsed.sort()
        picks = [s for _, _, s in parsed[:CONTRACTS_PER_TICKER]]
        if not picks:
            print(f"{tk}: no near-dated ATM contracts")
            continue

        rows = []
        for sym in picks:
            body = get(f"/api/option-contract/{sym}/historic", key)
            time.sleep(PAUSE)
            for ch in (body or {}).get("chains") or []:
                bid, ask = ch.get("nbbo_bid"), ch.get("nbbo_ask")
                vol = ch.get("volume") or 0
                if bid is None or ask is None:
                    continue
                try:
                    bid, ask = float(bid), float(ask)
                except (TypeError, ValueError):
                    continue
                mid = (bid + ask) / 2.0
                # Only contracts in the band the desk trades, and only ones that
                # actually traded: an untraded contract's quote is theoretical.
                if mid <= 0 or not (1.00 <= mid <= 3.50) or int(vol) < 10:
                    continue
                rows.append(
                    {"day": ch.get("date"), "sym": sym, "mid": mid,
                     "spread_pct": (ask - bid) / mid * 100.0, "volume": int(vol)}
                )
        results[tk] = rows
        med = median(r["spread_pct"] for r in rows) if rows else float("nan")
        print(f"{tk:<6} contracts={len(picks):<3} quotes={len(rows):<5} median round trip {med:6.2f}%")

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")

    print("\n=== ranked, median round trip (% of premium) ===")
    from pa.open_session.bandit import LEVERAGE  # noqa: E402

    print(f"  {'ticker':<8} {'quotes':>7} {'round trip':>11} {'bar':>9}")
    for tk, rows in sorted(results.items(), key=lambda kv: median(
            (r["spread_pct"] for r in kv[1]), ) if kv[1] else 1e9):
        if not rows:
            continue
        rt = median(r["spread_pct"] for r in rows)
        print(f"  {tk:<8} {len(rows):>7} {rt:>10.2f}% {rt / LEVERAGE:>8.4f}%")


if __name__ == "__main__":
    main()
