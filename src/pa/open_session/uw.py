"""Real-time option NBBO from Unusual Whales.

Yahoo's option chain runs ~15 minutes behind (verified Sep 10: Yahoo quoted
NFLX 76C at 0.59 while the live NBBO was 0.73). That delay silently corrupted
every mark, every exit fill and therefore the whole P&L record, so UW is the
primary quote source and Yahoo is only the fallback.

One request returns every contract for a ticker, so the chain is cached per
ticker for a scan cadence rather than fetched per row.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

log = logging.getLogger("pa.uw")

BASE = "https://api.unusualwhales.com/api/stock/{ticker}/option-chains?greeks=true"
TIMEOUT = 10
CACHE_SECONDS = 20

# ticker -> (monotonic_stamp, {(strike, right): quote})
_CACHE: dict[str, tuple[float, dict]] = {}


def _key(settings=None) -> str:
    if settings is None:
        try:
            from pa.config import get_settings

            settings = get_settings()
        except Exception:
            return ""
    return (getattr(settings, "unusual_whales_api_key", "") or "").strip()


def enabled(settings=None) -> bool:
    return bool(_key(settings))


def _age_seconds(raw) -> float | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())


def _right(raw: str) -> str:
    return "C" if str(raw or "").lower().startswith("c") else "P"


def fetch_chain(ticker: str, *, settings=None, max_age: float | None = None) -> dict | None:
    """Every contract for one ticker, keyed by (expiry, strike, right).

    max_age overrides the scan cache. Exits pass 0: a stop checked against a
    quote from twenty seconds ago is the same stop that let SPY run from -23%
    to -33% between two looks on 2026-09-22. The scan keeps the longer cache,
    because it prices the whole watchlist and a new signal does not need to be
    that fresh.
    """
    token = _key(settings)
    if not token:
        return None
    ticker = ticker.upper()
    hit = _CACHE.get(ticker)
    now = time.monotonic()
    limit = CACHE_SECONDS if max_age is None else max_age
    if hit and now - hit[0] < limit:
        return hit[1]

    req = urllib.request.Request(
        BASE.format(ticker=ticker),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            rows = json.loads(resp.read().decode("utf-8")).get("data") or []
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        log.warning("unusual whales fetch failed for %s: %s", ticker, exc)
        return None

    out: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            strike = round(float(row.get("strike")), 4)
        except (TypeError, ValueError):
            continue
        expiry = str(row.get("expires") or "")[:10]
        if not expiry:
            continue
        bid = float(row.get("nbbo_bid") or 0)
        ask = float(row.get("nbbo_ask") or 0)
        mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else (bid or ask)
        out[(expiry, strike, _right(row.get("option_type")))] = {
            "bid": bid,
            "ask": ask,
            "mid": round(mid, 2) if mid else None,
            "volume": row.get("volume"),
            "open_interest": row.get("open_interest"),
            "delta": row.get("delta"),
            "age": _age_seconds(row.get("last_tape_time")),
        }
    _CACHE[ticker] = (now, out)
    return out


def pick_contract(
    ticker: str, spot: float, is_call: bool, on_or_after: date | str, *, settings=None
) -> dict | None:
    """Nearest-expiry, nearest-the-money contract straight off the live chain.

    Yahoo serves no chain at all for index names like SPX, which is why those
    signals used to fall back to a Black-Scholes guess. UW has them, so the
    strike and the fill both come from real quotes.
    """
    chain = fetch_chain(ticker, settings=settings)
    if not chain:
        return None
    want = on_or_after.isoformat() if isinstance(on_or_after, date) else str(on_or_after)[:10]
    right = "C" if is_call else "P"
    expiries = sorted({key[0] for key in chain if key[2] == right and key[0] >= want})
    if not expiries:
        return None
    expiry = expiries[0]
    live = [
        (key, val)
        for key, val in chain.items()
        if key[0] == expiry and key[2] == right and (val.get("bid") or val.get("ask"))
    ]
    if not live:
        return None
    (_, strike, _), hit = min(live, key=lambda kv: abs(kv[0][1] - float(spot)))
    return {"expiry": expiry, "strike": strike, **hit}


def quote(
    ticker: str,
    strike: float,
    expiry: date | str,
    direction: str,
    *,
    settings=None,
    fresh: bool = False,
) -> dict | None:
    """NBBO for one contract, or None when UW has nothing usable.

    fresh bypasses the chain cache. Open positions use it; a scan does not.
    """
    chain = fetch_chain(ticker, settings=settings, max_age=0 if fresh else None)
    if not chain:
        return None
    if isinstance(expiry, date):
        expiry = expiry.isoformat()
    key = (str(expiry)[:10], round(float(strike), 4), _right(direction))
    hit = chain.get(key)
    if not hit:
        return None
    if not (hit.get("bid") or hit.get("ask")):
        return None
    return hit
