"""Push Buy and live Sell lines to Telegram the instant they print.

TAKE always goes out. WATCH rides along when PA_TELEGRAM_WATCH is on and is
prefixed "WATCH " so the two are never confused in the chat.

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


def send_signal(text: str, *, settings: Settings | None = None, monospace: bool = False) -> bool:
    """Post one Buy/Sell line. Returns True only when Telegram accepted it.

    monospace wraps the body in <pre> so column-aligned blocks (the EOD summary)
    keep their spacing instead of collapsing in Telegram's proportional font.
    """
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
    body = f"<pre>{_html(line)}</pre>" if monospace else _html(line)
    try:
        if _post({**base, "text": body, "parse_mode": "HTML"}):
            return True
        return _post({**base, "text": line})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        log.warning("telegram send failed: %s", exc)
        return False


ENTRY_VERDICTS = frozenset({"TAKE", "EXECUTE"})
EXPIRY_REASONS = frozenset({"expired", "expired_unquoted"})


def wants_verdict(verdict: str, settings: Settings) -> bool:
    """TAKE always goes out; WATCH only when PA_TELEGRAM_WATCH is on."""
    v = str(verdict or "").upper()
    if v in ENTRY_VERDICTS:
        return True
    return v == "WATCH" and bool(getattr(settings, "telegram_watch", False))


STUDY_BANNER = "[STUDY - DO NOT TRADE] "


def _tag(verdict: str, settings: Settings | None = None) -> str:
    """Prefix WATCH so it can never be mistaken for a TAKE. TAKE stays untouched.

    In study mode everything is banded, TAKE included, because in that mode the
    distinction between the two carries no money and the only thing that matters
    is that no line looks actionable.
    """
    prefix = STUDY_BANNER if settings is not None and getattr(settings, "study_mode", False) else ""
    return prefix + ("WATCH " if str(verdict or "").upper() == "WATCH" else "")


def notify_take(row: dict, *, settings: Settings | None = None) -> bool:
    """Fire an entry with a real Buy line — TAKE always, WATCH when enabled.

    Records telegram_sent on success, the mirror of telegram_sold on the exit
    side. The flag lives in the book, so it is what makes an entry alert survive
    a restart: without it the only thing stopping a duplicate Buy was the row
    never being revisited, and the only thing a failed send left behind was
    nothing at all.
    """
    if settings is None or not enabled(settings):
        return False
    if row.get("telegram_sent"):
        return False
    verdict = str(row.get("verdict") or "").upper()
    if not wants_verdict(verdict, settings):
        return False
    text = str(row.get("text_buy") or "").strip()
    if not text.lower().startswith("buy "):
        return False
    ok = send_signal(_tag(verdict, settings) + text, settings=settings)
    if ok:
        row["telegram_sent"] = True
    return ok


def notify_sell(row: dict, *, settings: Settings | None = None) -> bool:
    """Fire the live Sell fill the moment the desk closes a TAKE — never a target preview."""
    if settings is None or not enabled(settings):
        return False
    if row.get("telegram_sold"):
        return False
    # An expiry close is bookkeeping, not a trade. Alerting on a contract that
    # expired days ago is noise you cannot act on.
    if str(row.get("reason") or "") in EXPIRY_REASONS:
        return False
    verdict = str(row.get("verdict") or "").upper()
    if not wants_verdict(verdict, settings):
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
        pnl_dollars=row.get("pnl_dollars"),
        reason=str(row.get("reason") or ""),
    )
    if not text.lower().startswith("sell "):
        return False
    ok = send_signal(_tag(verdict, settings) + text, settings=settings)
    if ok:
        row["telegram_sold"] = True
    return ok
