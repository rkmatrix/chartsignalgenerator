from __future__ import annotations

import re
from datetime import datetime

from pa.domain.models import AssetClass, Side, Signal

_TICKER = re.compile(r"\b([A-Z]{1,5})\b")


class ChatSession:
    def __init__(self) -> None:
        self.ticker: str | None = None
        self.pending: dict | None = None
        self.log: list[dict] = []


def parse_command(text: str, now: datetime, last_ticker: str | None = None) -> dict:
    raw = text.strip()
    low = raw.lower()
    tickers = [t for t in _TICKER.findall(raw.upper()) if t not in {"BUY", "SELL", "FLAT", "CALL", "PUT"}]
    ticker = tickers[0] if tickers else last_ticker
    if low in {"status", "help", "why", "briefing", "flatten", "kill", "pause", "resume"}:
        return {"action": low, "ticker": ticker, "text": raw}
    qty = 1
    qty_m = re.search(r"\b(\d+)\b", low)
    if qty_m:
        qty = max(1, int(qty_m.group(1)))
    if low.startswith("buy") or "long" in low:
        return {
            "action": "signal",
            "ticker": ticker,
            "qty": qty,
            "side": Side.BUY,
            "asset_class": AssetClass.OPTION if "call" in low or "put" in low else AssetClass.ETF,
            "text": raw,
        }
    if low.startswith("sell") or "short" in low:
        return {
            "action": "signal",
            "ticker": ticker,
            "qty": qty,
            "side": Side.SELL,
            "asset_class": AssetClass.ETF,
            "text": raw,
        }
    return {"action": "chat", "ticker": ticker, "text": raw}


def command_to_signal(cmd: dict, now: datetime) -> Signal | None:
    if cmd.get("action") != "signal" or not cmd.get("ticker"):
        return None
    return Signal(
        ticker=cmd["ticker"],
        side=cmd["side"],
        confidence=0.9,
        reasons=[f"chat:{cmd.get('text', '')}"],
        source="voice",
        ts=now,
        asset_class=cmd.get("asset_class", AssetClass.ETF),
    )
