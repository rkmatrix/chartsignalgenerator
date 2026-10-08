"""Send the desk's option opens and closes to a Webull paper account.

The host is fixed to Webull's sandbox. A live host is rejected before a client
is built, the same way TRADING_MODE other than paper is rejected at startup.
Options are long only: buy to open, sell to close, limit orders, because the
options API does not take a market order.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from pa.config import LiveTradingBlocked

log = logging.getLogger("pa.webull")

PAPER_HOST = "api.sandbox.webull.com"


def _host(settings) -> str:
    raw = str(getattr(settings, "webull_host", "") or PAPER_HOST).strip().lower()
    raw = raw.removeprefix("https://").removeprefix("http://").split("/")[0]
    if raw != PAPER_HOST:
        raise LiveTradingBlocked(
            f"WEBULL_HOST={raw!r} is blocked. Paper orders go only to {PAPER_HOST}."
        )
    return raw


def configured(settings) -> bool:
    """True when the operator asked for Webull and both secrets are present."""
    if settings is None:
        return False
    if str(getattr(settings, "paper_broker", "") or "").strip().lower() != "webull":
        return False
    key = str(getattr(settings, "webull_app_key", "") or "").strip()
    secret = str(getattr(settings, "webull_app_secret", "") or "").strip()
    if not key or not secret:
        return False
    _host(settings)
    return True


def _client_order_id() -> str:
    # Webull rejects anything longer than 32 characters.
    return uuid4().hex


def _option_type(row: dict) -> str:
    raw = str(row.get("direction") or row.get("opt") or "").lower()
    if raw.startswith("p"):
        return "PUT"
    return "CALL"


def option_order(row: dict, *, side: str, limit: float, client_order_id: str) -> dict | None:
    """One single-leg limit order, or None when the row has no contract."""
    ticker = str(row.get("ticker") or "").upper()
    strike = row.get("strike")
    expiry = str(row.get("expiry") or "")[:10]
    if not ticker or strike is None or len(expiry) != 10 or limit <= 0:
        return None
    qty = max(1, int(row.get("contracts") or 1))
    side = side.upper()
    intent = "BUY_TO_OPEN" if side == "BUY" else "SELL_TO_CLOSE"
    leg_side = "BUY" if side == "BUY" else "SELL"
    return {
        "client_order_id": client_order_id,
        "combo_type": "NORMAL",
        "order_type": "LIMIT",
        "limit_price": f"{float(limit):.2f}",
        "quantity": str(qty),
        "option_strategy": "SINGLE",
        "side": side,
        "position_intent": intent,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "instrument_type": "OPTION",
        "market": "US",
        "symbol": ticker,
        "legs": [
            {
                "side": leg_side,
                "quantity": str(qty),
                "symbol": ticker,
                "strike_price": f"{float(strike):.2f}",
                "option_expire_date": expiry,
                "instrument_type": "OPTION",
                "option_type": _option_type(row),
                "market": "US",
            }
        ],
    }


# The same keys see Events Cash, Futures and Crypto accounts too, and Events
# Cash is listed first. Previews pass on all of them; placing an option order
# on anything but an individual account is refused with 417
# OPENAPI_OPTION_STRATEGY_NOT_MATCH_ANY.
OPTION_ACCOUNT_CLASSES = ("INDIVIDUAL_MARGIN", "INDIVIDUAL_CASH")


def _accounts_in(payload) -> list[dict]:
    if isinstance(payload, dict):
        if payload.get("account_id") or payload.get("accountId"):
            return [payload]
        found: list[dict] = []
        for value in payload.values():
            found += _accounts_in(value)
        return found
    if isinstance(payload, list):
        found = []
        for item in payload:
            found += _accounts_in(item)
        return found
    return []


def _account_id_from(payload) -> str:
    """The account that can hold options, by class; empty when there is none."""
    accounts = _accounts_in(payload)
    for wanted in OPTION_ACCOUNT_CLASSES:
        for acct in accounts:
            if str(acct.get("account_class") or "").upper() == wanted:
                return str(acct.get("account_id") or acct.get("accountId"))
    if len(accounts) == 1 and not accounts[0].get("account_class"):
        return str(accounts[0].get("account_id") or accounts[0].get("accountId"))
    return ""


class WebullPaper:
    """Thin wrapper. The SDK is imported here so tests never need it installed."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.host = _host(settings)
        self._trade = None
        self._account_id = str(getattr(settings, "webull_account_id", "") or "").strip()

    def _trade_client(self):
        if self._trade is not None:
            return self._trade
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient

        client = ApiClient(
            str(self.settings.webull_app_key).strip(),
            str(self.settings.webull_app_secret).strip(),
            "us",
        )
        # TradeClient writes a log file in the working directory unless told not to.
        client._stream_logger_set = True
        client._file_logger_set = True
        client.add_endpoint("us", self.host)
        self._trade = TradeClient(client)
        return self._trade

    def account_id(self) -> str:
        if self._account_id:
            return self._account_id
        response = self._trade_client().account_v2.get_account_list()
        body = response.json() if hasattr(response, "json") else response
        found = _account_id_from(body)
        if not found:
            raise RuntimeError("Webull returned no paper account for these keys")
        self._account_id = found
        log.info("webull paper account %s", found)
        return found

    def place(self, order: dict) -> tuple[bool, str]:
        response = self._trade_client().order_v3.place_order(self.account_id(), [order])
        status = getattr(response, "status_code", 0)
        try:
            body = response.json() if hasattr(response, "json") else str(response)
        except Exception:
            body = getattr(response, "text", "")
        ok = 200 <= int(status) < 300
        if not ok:
            log.warning("webull paper order refused status=%s body=%s", status, str(body)[:300])
        return ok, str(body)[:300]


def _remember(row: dict, field: str) -> str:
    current = str(row.get(field) or "")
    if current:
        return current
    fresh = _client_order_id()
    row[field] = fresh
    return fresh


def mirror_open(row: dict, settings) -> None:
    """Buy the contract the desk just opened. A repeat uses the same client id."""
    if row.get("webull_buy_sent") or not configured(settings):
        return
    limit = float(row.get("entry") or 0)
    order = option_order(row, side="BUY", limit=limit, client_order_id=_remember(row, "webull_buy_id"))
    if order is None:
        return
    try:
        ok, detail = WebullPaper(settings).place(order)
    except LiveTradingBlocked:
        raise
    except Exception as exc:
        row["webull_error"] = str(exc)[:300]
        log.warning("webull paper buy failed: %s", exc)
        return
    row["webull_buy_sent"] = ok
    if not ok:
        row["webull_error"] = detail


def mirror_close(row: dict, settings) -> None:
    """Sell a contract this desk bought. Never sells something it did not open."""
    if not row.get("webull_buy_sent") or row.get("webull_sell_sent") or not configured(settings):
        return
    limit = float(row.get("exit") or row.get("bid") or 0)
    order = option_order(row, side="SELL", limit=limit, client_order_id=_remember(row, "webull_sell_id"))
    if order is None:
        return
    try:
        ok, detail = WebullPaper(settings).place(order)
    except LiveTradingBlocked:
        raise
    except Exception as exc:
        row["webull_error"] = str(exc)[:300]
        log.warning("webull paper sell failed: %s", exc)
        return
    row["webull_sell_sent"] = ok
    if not ok:
        row["webull_error"] = detail
