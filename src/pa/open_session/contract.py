from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.chart_trader.monday import (
    expiry_label,
    fetch_chain_rows,
    pick_from_chain,
    round_strike,
    synthetic_premium,
    take_profit_pct,
)

ET = ZoneInfo("America/New_York")
CACHE_SECONDS = 180
QUOTE_CHAIN_SECONDS = 60
_CHAIN_CACHE: dict[str, tuple[float, tuple]] = {}


def strike_txt(strike: float) -> str:
    return str(int(strike)) if float(strike).is_integer() else f"{strike:g}"


def right_label(direction: str) -> str:
    return "Call" if str(direction).lower().startswith("c") else "Put"


def format_buy(ticker: str, strike: float, direction: str, expiry: date, entry: float) -> str:
    return (
        f"Buy {ticker} {strike_txt(strike)} {right_label(direction)} "
        f"Exp {expiry_label(expiry)} for ${entry:.2f}"
    )


def format_sell(ticker: str, strike: float, direction: str, price: float, tp_pct: float) -> str:
    return (
        f"Sell {ticker} {strike_txt(strike)} {right_label(direction)} "
        f"for ${price:.2f} Take Profit {tp_pct:.0f}%"
    )


def format_pl(pnl_pct: float | None, pnl_dollars: float | None) -> str:
    """Signed P/L for one contract, e.g. '+55% (+$60.00)'."""
    bits: list[str] = []
    if pnl_pct is not None:
        bits.append(f"{float(pnl_pct):+.0f}%")
    if pnl_dollars is not None:
        amount = float(pnl_dollars)
        bits.append(f"({'-' if amount < 0 else '+'}${abs(amount):,.2f})")
    return " ".join(bits)


def format_exit_sell(
    ticker: str,
    strike: float,
    direction: str,
    expiry: date | str,
    price: float,
    *,
    pnl_pct: float | None = None,
    pnl_dollars: float | None = None,
    reason: str = "",
) -> str:
    """Actionable Sell line at the live fill — same contract identity as the Buy.

    P/L rides on every exit, not just the winners, so a stop reads as a number
    instead of a bare 'Stop'.
    """
    if isinstance(expiry, str):
        expiry = date.fromisoformat(str(expiry)[:10])
    line = (
        f"Sell {ticker} {strike_txt(strike)} {right_label(direction)} "
        f"Exp {expiry_label(expiry)} for ${float(price):.2f}"
    )
    why = str(reason or "")
    if why == "take_profit" or (why == "" and pnl_pct is not None and pnl_pct > 0):
        label = "Take Profit"
    elif why == "time_stop":
        label = "Time stop"
    elif why:
        label = "Stop"
    else:
        label = "P/L"
    pl = format_pl(pnl_pct, pnl_dollars)
    if not pl:
        return line if label == "P/L" else f"{line} {label}"
    return f"{line} {label} {pl}"


def texts_from_contract(
    ticker: str,
    strike: float,
    direction: str,
    expiry: date | str,
    entry: float,
    target: float,
    tp_pct: float,
) -> tuple[str, str]:
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry[:10])
    return (
        format_buy(ticker, strike, direction, expiry, entry),
        format_sell(ticker, strike, direction, target, tp_pct),
    )


def _cache_path(data_dir: Path) -> Path:
    return data_dir / "history" / "contracts_cache.json"


def _load_cache(data_dir: Path) -> dict:
    path = _cache_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data_dir: Path, payload: dict) -> None:
    path = _cache_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def contract_for(
    ticker: str,
    direction: str,
    spot: float,
    now: datetime,
    data_dir: Path,
    *,
    fetch: bool = True,
    conviction: float = 1.0,
) -> dict:
    """ATM-ish 0DTE (or nearest expiry) contract for a fused Call/Put."""
    ticker = ticker.upper()
    is_call = direction == "call"
    today = now.astimezone(ET).date() if now.tzinfo else now.date()
    key = f"{ticker}-{direction}-{today.isoformat()}"
    cache = _load_cache(data_dir)
    hit = cache.get(key) or {}
    ts = hit.get("cached_at")
    if ts:
        try:
            age = datetime.now().timestamp() - datetime.fromisoformat(ts).timestamp()
            if age < CACHE_SECONDS and hit.get("entry"):
                return hit
        except Exception:
            pass

    expiry = today
    picked = None
    source = "model"
    strike = None
    entry = None

    # Live NBBO first. Buying lifts the ask, so that is the fill we record.
    if fetch:
        from pa.open_session import uw

        live = uw.pick_contract(ticker, float(spot), is_call, today)
        fill = None if not live else (live.get("ask") or live.get("mid") or live.get("bid"))
        if live and fill:
            expiry = date.fromisoformat(live["expiry"])
            strike = float(live["strike"])
            entry = float(fill)
            source = "uw"

    if entry is None and fetch:
        try:
            expiry, calls, puts = fetch_chain_rows(ticker, today, prefer_exact=True)
            picked = pick_from_chain(calls if is_call else puts, spot, is_call)
            if picked:
                source = picked.get("source") or "chain"
        except Exception:
            picked = None

    if strike is None:
        strike = float(picked["strike"]) if picked else round_strike(spot)
    if entry is None:
        entry = float(picked["entry"]) if picked else synthetic_premium(spot, strike, expiry, today, is_call)
    tp = take_profit_pct(0.5, min(1.0, max(0.35, conviction / 3.0)))
    target = round(entry * (1.0 + tp / 100.0), 2)
    row = {
        "ticker": ticker,
        "direction": direction,
        "right": right_label(direction).lower(),
        "opt": "C" if is_call else "P",
        "strike": strike,
        "expiry": expiry.isoformat(),
        "entry": round(entry, 2),
        "target": target,
        "take_profit_pct": tp,
        "spot": round(float(spot), 2),
        "premium_source": source,
        "cached_at": datetime.now().isoformat(),
    }
    row["text_buy"], row["text_sell"] = texts_from_contract(
        ticker, strike, direction, expiry, entry, target, tp
    )
    cache[key] = row
    _save_cache(data_dir, cache)
    return row


def quote_detail(
    ticker: str,
    strike: float,
    expiry: date | str,
    direction: str,
    *,
    fetch: bool = True,
    settings=None,
) -> dict | None:
    """Live NBBO for one contract: bid, ask, mid, source and quote age.

    Unusual Whales first (real-time NBBO); Yahoo only as a fallback, and Yahoo's
    chain runs ~15 minutes late so anything sourced there is marked stale.
    """
    if not fetch or strike is None or not expiry:
        return None
    day = expiry if isinstance(expiry, str) else expiry.isoformat()

    from pa.open_session import uw

    hit = uw.quote(ticker, float(strike), day, direction, settings=settings)
    if hit:
        return {
            "bid": hit.get("bid") or None,
            "ask": hit.get("ask") or None,
            "mid": hit.get("mid"),
            "source": "uw",
            "age": hit.get("age"),
        }

    stale = _yahoo_quote(ticker, float(strike), day, direction)
    if stale is None:
        return None
    return {**stale, "source": "yahoo", "age": None}


def quote_mark(
    ticker: str,
    strike: float,
    expiry: date | str,
    direction: str,
    *,
    fetch: bool = True,
    settings=None,
) -> float | None:
    """Mark-to-market price of the printed strike/expiry (NBBO mid when available)."""
    hit = quote_detail(ticker, strike, expiry, direction, fetch=fetch, settings=settings)
    if not hit:
        return None
    px = hit.get("mid") or hit.get("bid") or hit.get("ask")
    return round(float(px), 2) if px else None


def _yahoo_quote(
    ticker: str,
    strike: float,
    expiry: date | str,
    direction: str,
) -> dict | None:
    """Fallback chain read. Delayed ~15 minutes — never treat this as live."""
    if isinstance(expiry, str):
        expiry = date.fromisoformat(str(expiry)[:10])
    packed = _chain_rows(ticker, expiry)
    if packed is None:
        return None
    exp, calls, puts = packed
    if exp != expiry:
        return None
    is_call = str(direction).lower().startswith("c")
    rows = calls if is_call else puts
    ranked = sorted(rows, key=lambda r: abs(float(r["strike"]) - float(strike)))
    if not ranked or abs(float(ranked[0]["strike"]) - float(strike)) > 0.51:
        return None
    hit = ranked[0]
    bid = float(hit.get("bid") or 0)
    last = float(hit.get("last") or 0)
    ask = float(hit.get("ask") or 0)
    mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0.0
    px = mid or bid or last or ask
    if px <= 0:
        return None
    return {"bid": bid or None, "ask": ask or None, "mid": round(px, 2)}


def _chain_rows(ticker: str, expiry: date) -> tuple | None:
    key = f"{ticker.upper()}-{expiry.isoformat()}"
    hit = _CHAIN_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < QUOTE_CHAIN_SECONDS:
        return hit[1]
    try:
        packed = fetch_chain_rows(ticker, expiry, prefer_exact=True)
    except Exception:
        return None
    if packed[0] != expiry:
        _CHAIN_CACHE[key] = (now, None)
        return None
    _CHAIN_CACHE[key] = (now, packed)
    return packed
