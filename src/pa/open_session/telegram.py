"""Push TAKE Buy and live Sell lines to Telegram the instant they print.

Same path as SignalValidator's signal_agent alerts: one blocking sendMessage
so the chat is not waiting on a queue. Missing token/chat is a silent no-op.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from pa.config import Settings, get_settings

log = logging.getLogger("pa.telegram")
TIMEOUT = 5


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def enabled(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool((s.telegram_bot_token or "").strip() and (s.telegram_chat_id or "").strip())


def send_signal(text: str, *, settings: Settings | None = None) -> bool:
    """Post one Buy/Sell line. Returns True only when Telegram accepted it."""
    line = (text or "").strip()
    if not line:
        return False
    s = settings or get_settings()
    token = (s.telegram_bot_token or "").strip()
    chat_id = (s.telegram_chat_id or "").strip()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def _post(payload: dict) -> bool:
        raw = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        ok = bool(body.get("ok"))
        if not ok:
            log.warning("telegram rejected: %s", body.get("description") or body)
        return ok

    base = {"chat_id": chat_id, "disable_web_page_preview": True}
    try:
        if _post({**base, "text": _html(line), "parse_mode": "HTML"}):
            return True
        return _post({**base, "text": line})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        log.warning("telegram send failed: %s", exc)
        return False


def notify_take(row: dict, *, settings: Settings | None = None) -> bool:
    """Fire only a TAKE with a real Buy line — never WATCH, never a reprint."""
    if settings is None or not enabled(settings):
        return False
    if str(row.get("verdict") or "").upper() != "TAKE":
        return False
    text = str(row.get("text_buy") or "").strip()
    if not text.lower().startswith("buy "):
        return False
    return send_signal(text, settings=settings)


def notify_sell(row: dict, *, settings: Settings | None = None) -> bool:
    """Fire the live Sell fill the moment the desk closes a TAKE — never a target preview."""
    if settings is None or not enabled(settings):
        return False
    if row.get("telegram_sold"):
        return False
    if str(row.get("verdict") or "").upper() not in {"TAKE", "EXECUTE"}:
        return False
    if row.get("status") != "closed" or row.get("exit") is None:
        return False
    ticker = row.get("ticker")
    strike = row.get("strike")
    expiry = row.get("expiry")
    if ticker is None or strike is None or not expiry:
        return False
    from pa.open_session.contract import format_exit_sell

    text = format_exit_sell(
        str(ticker),
        float(strike),
        str(row.get("direction") or row.get("opt") or ""),
        expiry,
        float(row["exit"]),
        pnl_pct=row.get("pnl_pct"),
        reason=str(row.get("reason") or ""),
    )
    if not text.lower().startswith("sell "):
        return False
    ok = send_signal(text, settings=settings)
    if ok:
        row["telegram_sold"] = True
    return ok
