from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pa.domain.models import Side
from pa.quant.pricing import black_scholes

ET = ZoneInfo("America/New_York")


def next_monday_open(now: datetime) -> date:
    """Coming Monday regular-hours open (this Monday if before 09:30 ET)."""
    local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    day = local.date()
    clock = local.time()
    weekday = day.weekday()
    if weekday == 0 and clock < time(9, 30):
        return day
    ahead = (7 - weekday) % 7
    if ahead == 0:
        ahead = 7
    return day + timedelta(days=ahead)


def round_strike(spot: float) -> float:
    if spot >= 200:
        return float(int(round(spot / 5.0) * 5))
    if spot >= 50:
        return float(int(round(spot)))
    return round(spot * 2) / 2.0


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n <= 0:
        return 0.0, 0.0, 0.0
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n) / denom
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    return lo, center, hi


def confidence_label(oos_n: int, wilson_lo: float) -> str:
    if oos_n >= 12 and wilson_lo >= 0.45:
        return "high"
    if oos_n >= 8 and wilson_lo >= 0.35:
        return "medium"
    return "low"


def take_profit_pct(oos_precision: float, last_confidence: float) -> float:
    blended = 0.6 * oos_precision + 0.4 * last_confidence
    return round(25.0 + 50.0 * max(0.0, min(1.0, blended)), 1)


def expiry_label(expiry: date) -> str:
    return f"{expiry.month}/{expiry.day}"


def synthetic_premium(spot: float, strike: float, expiry: date, now: date, call: bool, iv: float = 0.32) -> float:
    days = max((expiry - now).days, 1)
    years = days / 365.0
    price = black_scholes(spot, strike, years, 0.04, iv, call)
    return max(0.10, round(price, 2))


def pick_from_chain(rows: list[dict], spot: float, call: bool) -> dict | None:
    """rows: strike, bid, ask, last"""
    if not rows:
        return None
    want = round_strike(spot)
    ranked = sorted(rows, key=lambda r: abs(float(r["strike"]) - want))
    row = ranked[0]
    last = float(row.get("last") or 0)
    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    entry = ask if ask > 0 else (last if last > 0 else (bid if bid > 0 else 0.0))
    if entry <= 0:
        return None
    return {
        "strike": float(row["strike"]),
        "entry": round(entry, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "last": round(last, 2),
        "source": "chain",
        "right": "call" if call else "put",
    }


def fetch_chain_rows(ticker: str, expiry: date, prefer_exact: bool = False) -> tuple[date, list[dict], list[dict]]:
    import yfinance as yf

    stock = yf.Ticker(ticker)
    expiries = list(stock.options or [])
    if not expiries:
        raise RuntimeError("no option expiries")
    want = expiry.isoformat()
    if prefer_exact and want in expiries:
        chosen = want
    else:
        chosen = min(expiries, key=lambda e: abs((date.fromisoformat(e) - expiry).days))
    chain = stock.option_chain(chosen)
    exp = date.fromisoformat(chosen)

    def pack(frame) -> list[dict]:
        out = []
        for _, row in frame.iterrows():
            out.append(
                {
                    "strike": float(row["strike"]),
                    "bid": float(row.get("bid") or 0),
                    "ask": float(row.get("ask") or 0),
                    "last": float(row.get("lastPrice") or 0),
                }
            )
        return out

    return exp, pack(chain.calls), pack(chain.puts)


def option_card(
    ticker: str,
    side: Side,
    spot: float,
    monday: date,
    asof: date,
    oos_precision: float,
    last_confidence: float,
    oos_n: int,
    wilson_lo: float,
    chain_calls: list[dict] | None = None,
    chain_puts: list[dict] | None = None,
    chain_expiry: date | None = None,
) -> dict:
    call = side == Side.BUY
    right = "Call" if call else "Put"
    expiry = chain_expiry or monday
    picked = pick_from_chain(chain_calls if call else (chain_puts or []), spot, call)
    strike = picked["strike"] if picked else round_strike(spot)
    entry = picked["entry"] if picked else synthetic_premium(spot, strike, expiry, asof, call)
    tp = take_profit_pct(oos_precision, last_confidence)
    target = round(entry * (1.0 + tp / 100.0), 2)
    exp = expiry_label(expiry)
    strike_txt = str(int(strike)) if float(strike).is_integer() else f"{strike:g}"
    return {
        "ticker": ticker,
        "side": side.value,
        "right": right.lower(),
        "strike": strike,
        "expiry": expiry.isoformat(),
        "entry": entry,
        "target": target,
        "take_profit_pct": tp,
        "accuracy_pct": round(oos_precision * 100.0, 1),
        "confidence_label": confidence_label(oos_n, wilson_lo),
        "last_confidence": round(last_confidence, 3),
        "spot": round(spot, 2),
        "premium_source": picked["source"] if picked else "model",
        "text_buy": f"Buy {ticker} {strike_txt} {right} Exp {exp} for ${entry:.2f}",
        "text_sell": f"Sell {ticker} {strike_txt} {right} Exp {exp} for ${target:.2f}",
        "text_tp": f"Take profit {tp:.1f}%",
    }
