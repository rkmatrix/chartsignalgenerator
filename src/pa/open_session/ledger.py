from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.babysitter.advise import advise
from pa.open_session.calibrate import PLAN_HOLD_MINUTES
from pa.open_session.contract import texts_from_contract
from pa.open_session.grade import prediction_for, score_for, verdict_for

ET = ZoneInfo("America/New_York")


def book_path(data_dir: Path) -> Path:
    return data_dir / "history" / "signal_book.json"


def _bak_path(data_dir: Path) -> Path:
    return data_dir / "history" / "signal_book.bak.json"


def _trades_from_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        rows = data
    else:
        rows = list((data or {}).get("trades") or [])
    return [t for t in rows if isinstance(t, dict) and t.get("id")]


def load_book(data_dir: Path) -> dict:
    rows = _trades_from_file(book_path(data_dir))
    if not rows:
        rows = _trades_from_file(_bak_path(data_dir))
    return {"trades": rows}


def save_book(data_dir: Path, book: dict) -> dict:
    """Persist the tape. Incoming rows win by id; missing ids from disk/bak are kept."""
    path = book_path(data_dir)
    bak = _bak_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, dict] = {}
    for src in (_trades_from_file(bak), _trades_from_file(path), list(book.get("trades") or [])):
        for row in src:
            tid = row.get("id")
            if tid:
                merged[str(tid)] = row
    book = {"trades": list(merged.values())}
    payload = json.dumps(book, default=str, indent=2)
    tmp = path.with_name("signal_book.json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    if path.exists():
        try:
            bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    os.replace(tmp, path)
    return book


def _rewrite_texts(row: dict) -> None:
    strike = row.get("strike")
    entry = row.get("entry")
    expiry = row.get("expiry")
    ticker = row.get("ticker")
    if ticker is None or strike is None or entry is None or not expiry:
        return
    target = row.get("target")
    tp = float(row.get("take_profit_pct") or 0)
    if target is None:
        target = round(float(entry) * (1.0 + tp / 100.0), 2)
        row["target"] = target
    direction = row.get("direction") or ("put" if row.get("opt") == "P" else "call")
    closed = row.get("status") == "closed" and row.get("exit") is not None
    if closed:
        price = float(row["exit"])
        sell_pct = float(row.get("pnl_pct") or 0) if row.get("prediction") == "FAIL" else tp
    else:
        price = float(target)
        sell_pct = tp
    row["text_buy"], row["text_sell"] = texts_from_contract(
        ticker, float(strike), direction, expiry, float(entry), price, sell_pct
    )


def _apply_contract(row: dict, sig: dict) -> None:
    """Keep strike/expiry/entry as the fill; later quotes only update the live mark."""
    printed = row.get("strike") is not None and row.get("entry") is not None and row.get("expiry")
    if not printed:
        for key in ("strike", "opt", "expiry", "entry", "target", "take_profit_pct", "premium_source"):
            if sig.get(key) is not None:
                row[key] = sig[key]
        if sig.get("right"):
            row["right"] = sig["right"]
        if row.get("entry") is not None and row.get("mark") is None:
            row["mark"] = float(row["entry"])
    same_contract = (
        printed
        and sig.get("strike") is not None
        and row.get("strike") is not None
        and abs(float(sig["strike"]) - float(row["strike"])) < 1e-6
        and str(sig.get("expiry") or "")[:10] == str(row.get("expiry") or "")[:10]
    )
    if sig.get("entry") is not None and (not printed or same_contract):
        row["mark"] = float(sig["entry"])
        if printed and same_contract:
            row["quoted"] = True
    _rewrite_texts(row)


def _trade_id(ticker: str, direction: str, day: str) -> str:
    return f"{ticker}-{direction}-{day}"


def _row_session_day(row: dict) -> str:
    opened = str(row.get("opened_at") or "")[:10]
    if opened:
        return opened
    tid = str(row.get("id") or "")
    return tid[-10:] if len(tid) >= 10 else ""


def sides_taken_today(trades: list[dict], day: str) -> dict[str, str]:
    """First printed side per ticker this session. One TAKE per name per day."""
    out: dict[str, str] = {}
    for row in sorted(trades, key=lambda t: str(t.get("opened_at") or "")):
        if _row_session_day(row) != day:
            continue
        ticker = str(row.get("ticker") or "").upper()
        direction = str(row.get("direction") or "").lower()
        if ticker and direction in {"call", "put"} and ticker not in out:
            out[ticker] = direction
    return out


def other_side_block(ticker: str, direction: str, taken: dict[str, str]) -> str | None:
    ticker = ticker.upper()
    direction = str(direction or "").lower()
    prior = taken.get(ticker)
    if prior and prior != direction:
        return f"{ticker} already {prior} today — not taking the other side"
    return None


def open_take_tickers(trades: list[dict], day: str) -> set[str]:
    """Names with a live TAKE this session. One new TAKE at a time."""
    out: set[str] = set()
    for row in trades:
        if _row_session_day(row) != day or row.get("status") != "open":
            continue
        v = str(row.get("verdict") or "TAKE").upper()
        if v == "EXECUTE":
            v = "TAKE"
        if v != "TAKE":
            continue
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            out.add(ticker)
    return out


def invalidate_hunt_cache(data_dir: Path) -> None:
    """Drop strongest.json so the next hunt cannot reuse a pre-FAIL TAKE list."""
    path = data_dir / "history" / "strongest.json"
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        if path.exists():
            path.unlink()
    except OSError:
        pass


HOLD_MINUTES = 5
BREAKOUT_HOLD_MINUTES = 15
QUOTE_DEATH_MINUTES = 5
QUOTE_DEATH_PCT = -50.0
SELL_RETRY_MINUTES = 10


def _pnl(entry: float | None, exit_px: float | None) -> tuple[float | None, float | None]:
    if entry is None or exit_px is None or entry <= 0:
        return None, None
    dollars = round((exit_px - entry) * 100.0, 2)
    pct = round((exit_px - entry) / entry * 100.0, 1)
    return dollars, pct


def _apply_prediction(row: dict) -> None:
    """Prediction is decision quality vs P&L, not whether the chart exit was a take-profit."""
    if row.get("status") != "closed":
        return
    pct = row.get("pnl_pct")
    if pct is None:
        return
    row["prediction"] = prediction_for(row.get("verdict"), float(pct))


def _resolve_exit(row: dict, action: str) -> float | None:
    """Sell fill is the live bid/last only — never a modeled −25% or TP target."""
    mark = row.get("mark")
    if mark is None:
        return None
    if not row.get("quoted"):
        entry = row.get("entry")
        if entry is None or abs(float(mark) - float(entry)) < 1e-9:
            return None
    return round(float(mark), 2)


def _maybe_telegram_sell(row: dict, settings) -> None:
    if settings is None:
        return
    try:
        from pa.open_session.telegram import notify_sell

        notify_sell(row, settings=settings)
    except Exception:
        pass


def _recently_closed(row: dict, now: datetime) -> bool:
    raw = row.get("closed_at")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(str(raw))
    except Exception:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    return max(0.0, (now - ts).total_seconds() / 60.0) <= SELL_RETRY_MINUTES


def _sanitize_mark(row: dict, held: float) -> None:
    """A 0–5m −50% to −99% print is a quote death, not a fill. Do not mark or sell it."""
    entry = row.get("entry")
    mark = row.get("mark")
    if entry is None or mark is None or float(entry) <= 0:
        return
    if held >= QUOTE_DEATH_MINUTES:
        return
    pct = (float(mark) - float(entry)) / float(entry) * 100.0
    if pct <= QUOTE_DEATH_PCT:
        row["mark"] = float(entry)
        row["quoted"] = False
        row["quote_reject"] = True


def _minutes_held(row: dict, now: datetime) -> float:
    raw = row.get("opened_at")
    if not raw:
        return 0.0
    try:
        ts = datetime.fromisoformat(str(raw))
    except Exception:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    return max(0.0, (now - ts).total_seconds() / 60.0)


def _refresh_open_pnl(row: dict) -> None:
    if row.get("status") == "closed":
        return
    mark = row.get("mark")
    if mark is None:
        row["pnl_dollars"] = None
        row["pnl_pct"] = None
        return
    dollars, pct = _pnl(row.get("entry"), mark)
    row["pnl_dollars"] = dollars
    row["pnl_pct"] = pct
    peak = row.get("peak_mark")
    if peak is None or float(mark) > float(peak):
        row["peak_mark"] = float(mark)


def _quote_row(row: dict) -> float | None:
    if row.get("strike") is None or not row.get("expiry") or not row.get("ticker"):
        return None
    try:
        from pa.open_session.contract import quote_mark

        px = quote_mark(
            str(row["ticker"]),
            float(row["strike"]),
            row.get("expiry"),
            str(row.get("direction") or row.get("opt") or ""),
            fetch=True,
        )
    except Exception:
        return None
    if px is not None:
        row["mark"] = px
        row["quoted"] = True
    return px


def _is_sold(row: dict) -> bool:
    return row.get("status") == "closed" and row.get("exit") is not None


def _reopen(row: dict) -> None:
    row["status"] = "open"
    row["closed_at"] = None
    row["exit"] = None
    row["pnl_dollars"] = None
    row["pnl_pct"] = None
    row["reason"] = ""
    row["prediction"] = "pending"
    row["headline"] = ""


def _is_modeled_stop(row: dict) -> bool:
    """True when we booked −plan_stop% with no live option print."""
    if row.get("status") != "closed":
        return False
    if row.get("mark") is not None:
        return False
    entry = row.get("entry")
    exit_px = row.get("exit")
    plan = float(row.get("plan_stop_pct") or 0)
    if entry is None or exit_px is None or plan <= 0:
        return False
    expected = round(float(entry) * (1.0 - plan / 100.0), 2)
    return abs(float(exit_px) - expected) <= 0.03


def _heal_incomplete_close(row: dict) -> None:
    """A close is only valid with a real exit print. Modeled −25% fills are reopened."""
    if _is_modeled_stop(row) or (row.get("status") == "closed" and row.get("exit") is None):
        _reopen(row)
        return
    if row.get("status") != "closed":
        _refresh_open_pnl(row)
        return
    if row.get("pnl_dollars") is None or row.get("pnl_pct") is None:
        dollars, pct = _pnl(row.get("entry"), row.get("exit"))
        row["pnl_dollars"] = dollars
        row["pnl_pct"] = pct
    _apply_prediction(row)


def sync_book(
    data_dir: Path,
    signals: list[dict],
    now: datetime,
    levels_by_ticker: dict | None = None,
    minutes_to_close: int | None = None,
    quote_exit: bool = False,
    settings=None,
) -> dict:
    """Open a row when a fused signal prints; close it when the prediction fails or TP hits."""
    levels_by_ticker = levels_by_ticker or {}
    book = load_book(data_dir)
    trades: list[dict] = list(book.get("trades") or [])
    for row in trades:
        _heal_incomplete_close(row)
        _rewrite_texts(row)
    day = now.astimezone(ET).date().isoformat() if now.tzinfo else now.date().isoformat()

    by_id = {t["id"]: t for t in trades if t.get("id")}
    taken = sides_taken_today(trades, day)
    live_takes = open_take_tickers(trades, day)
    closed_now = False

    for sig in signals:
        tid = _trade_id(sig["ticker"], sig["direction"], day)
        if tid in by_id and by_id[tid].get("status") == "open":
            row = by_id[tid]
            row["stop"] = sig.get("stop")
            row["trigger"] = sig.get("trigger")
            _apply_contract(row, sig)
            _sanitize_mark(row, _minutes_held(row, now))
            continue
        if tid in by_id:
            continue
        blocked = other_side_block(str(sig.get("ticker") or ""), str(sig.get("direction") or ""), taken)
        if blocked:
            continue
        verdict = str(sig.get("verdict") or "TAKE").upper()
        if verdict == "EXECUTE":
            verdict = "TAKE"
        ticker_u = str(sig.get("ticker") or "").upper()
        window = str(sig.get("window") or "")
        if not window:
            from pa.open_session.clock import minutes_since_open
            from pa.open_session.playbook import session_window

            window = session_window(minutes_since_open(now)) or ""
        if verdict == "TAKE":
            from pa.open_session.calibrate import allows_take

            ok, _ = allows_take(
                window=window,
                direction=str(sig.get("direction") or ""),
                strategies=list(sig.get("strategies") or []),
                state=None,
            )
            if not ok:
                verdict = "WATCH"
        if verdict == "TAKE" and live_takes and ticker_u not in live_takes:
            continue
        opened = {
            "id": tid,
            "status": "open",
            "opened_at": now.isoformat(),
            "closed_at": None,
            "ticker": sig["ticker"],
            "direction": sig["direction"],
            "strike": sig.get("strike"),
            "opt": sig.get("opt") or ("C" if sig["direction"] == "call" else "P"),
            "expiry": sig.get("expiry"),
            "entry": sig.get("entry"),
            "mark": sig.get("entry"),
            "exit": None,
            "target": sig.get("target"),
            "take_profit_pct": sig.get("take_profit_pct"),
            "premium_source": sig.get("premium_source"),
            "pnl_dollars": None,
            "pnl_pct": None,
            "reason": "",
            "verdict": verdict if verdict in {"TAKE", "WATCH", "SKIP"} else (sig.get("verdict") or verdict_for(int(sig.get("score") or score_for(sig.get("conviction"))))),
            "score": int(sig.get("score") or score_for(sig.get("conviction"))),
            "prediction": "pending",
            "thesis": sig.get("thesis"),
            "stop": sig.get("stop"),
            "trigger": sig.get("trigger"),
            "plan_stop_pct": sig.get("plan_stop_pct"),
            "strategies": sig.get("strategies"),
            "families": sig.get("families"),
            "window": window,
            "playbook": sig.get("playbook") or "",
        }
        _rewrite_texts(opened)
        trades.append(opened)
        by_id[tid] = opened
        taken[str(sig["ticker"]).upper()] = str(sig["direction"]).lower()
        if str(opened.get("verdict") or "").upper() in {"TAKE", "EXECUTE"}:
            live_takes.add(str(sig["ticker"]).upper())
        try:
            from pa.open_session.telegram import notify_take

            notify_take(opened, settings=settings)
        except Exception:
            pass
        try:
            from pa.babysitter.feed import save_watch

            save_watch(
                data_dir,
                {
                    "ticker": sig["ticker"],
                    "right": sig.get("right") or sig["direction"],
                    "strike": sig.get("strike"),
                    "expiry": sig.get("expiry"),
                    "entry": sig.get("entry"),
                    "stop": sig.get("stop"),
                    "trigger": sig.get("trigger"),
                    "plan_stop_pct": sig.get("plan_stop_pct"),
                    "text_buy": opened.get("text_buy"),
                },
            )
        except Exception:
            pass

    for row in trades:
        if row.get("status") != "open":
            continue
        ticker = row.get("ticker")
        lv = levels_by_ticker.get(ticker) or {}
        last = lv.get("last")
        is_call = str(row.get("direction") or row.get("opt") or "C").lower().startswith("c")
        entry = float(row["entry"] or 0) or 0.01
        held = _minutes_held(row, now)
        if quote_exit:
            _quote_row(row)
        _sanitize_mark(row, held)
        _refresh_open_pnl(row)
        mark = float(row["mark"] if row.get("mark") is not None else entry)
        fused_now = lv.get("fused") or {}
        live_dir = str(fused_now.get("direction") or "")
        tape_direction = live_dir if live_dir in {"call", "put"} and live_dir != ("call" if is_call else "put") else None
        advice = advise(
            is_call=is_call,
            entry=entry,
            mark=mark,
            underlying=last,
            vwap=lv.get("vwap"),
            ema9=lv.get("ema9"),
            ema9_prev=lv.get("ema9_prev"),
            minutes_to_close=None,
            is_0dte=str(row.get("expiry") or "")[:10] == day,
            plan_stop_pct=row.get("plan_stop_pct"),
            underlying_stop=row.get("stop"),
            trigger=row.get("trigger"),
            tape_direction=tape_direction,
            minutes_held=held,
            peak_mark=row.get("peak_mark"),
        )
        if advice.action not in {"HARD_SELL", "STRONG_SELL", "TAKE_PROFIT"}:
            if (
                minutes_to_close is not None
                and minutes_to_close <= 20
                and str(row.get("expiry") or "")[:10] == day
            ):
                if quote_exit:
                    _quote_row(row)
                    _sanitize_mark(row, held)
                _close(row, now, exit_px=_resolve_exit(row, "HARD_SELL"), reason="time_stop", settings=settings)
                closed_now = True
            continue
        headline_l = (advice.headline or "").lower()
        is_tape_flip = "tape flipped" in headline_l
        is_breakout = ("breakout" in headline_l or "thesis broken" in headline_l) and not is_tape_flip
        is_plan = "plan stop" in headline_l
        if is_breakout and held < BREAKOUT_HOLD_MINUTES:
            continue
        if is_plan and held < PLAN_HOLD_MINUTES and not is_tape_flip:
            continue
        if held < HOLD_MINUTES and advice.action in {"HARD_SELL", "STRONG_SELL"} and not is_tape_flip:
            # First 5 minutes: no SL, no trigger. Plan-stop waits PLAN_HOLD_MINUTES. Quote deaths rejected above.
            continue
        reason = {
            "HARD_SELL": "underlying_stop" if "through SL" in (advice.headline or "") else "hard_stop",
            "STRONG_SELL": "failed_breakout" if "breakout" in (advice.headline or "").lower() else "thesis_broken",
            "TAKE_PROFIT": "take_profit",
        }.get(advice.action, advice.action.lower())
        if quote_exit:
            _quote_row(row)
        _close(
            row,
            now,
            exit_px=_resolve_exit(row, advice.action),
            reason=reason,
            headline=advice.headline,
            settings=settings,
        )
        closed_now = True

    if settings is not None:
        for row in trades:
            if row.get("telegram_sold") or not _is_sold(row) or not _recently_closed(row, now):
                continue
            if str(row.get("verdict") or "").upper() not in {"TAKE", "EXECUTE"}:
                continue
            _maybe_telegram_sell(row, settings)

    book["trades"] = trades
    saved = save_book(data_dir, book)
    if closed_now:
        invalidate_hunt_cache(data_dir)
    return saved


def watch_exits(
    data_dir: Path,
    now: datetime,
    levels_by_ticker: dict | None = None,
    minutes_to_close: int | None = None,
    quote_exit: bool = True,
    settings=None,
) -> dict:
    """Re-quote open prints and close when it's time to sell. Does not open new rows."""
    return sync_book(
        data_dir,
        [],
        now,
        levels_by_ticker=levels_by_ticker,
        minutes_to_close=minutes_to_close,
        quote_exit=quote_exit,
        settings=settings,
    )


def _close(
    row: dict,
    now: datetime,
    *,
    exit_px,
    reason: str,
    prediction: str | None = None,
    headline: str = "",
    settings=None,
) -> None:
    if exit_px is None:
        return
    row["status"] = "closed"
    row["closed_at"] = now.isoformat()
    row["reason"] = reason
    row["headline"] = headline
    row["exit"] = round(float(exit_px), 2)
    dollars, pct = _pnl(row.get("entry"), row["exit"])
    row["pnl_dollars"] = dollars
    row["pnl_pct"] = pct
    if prediction is not None:
        row["prediction"] = prediction
    _apply_prediction(row)
    _rewrite_texts(row)
    _maybe_telegram_sell(row, settings)


def repair_book(data_dir: Path) -> dict:
    """Rebuild Buy/Sell sentences and fill any close that is missing an exit print."""
    book = load_book(data_dir)
    for row in book.get("trades") or []:
        _heal_incomplete_close(row)
        _rewrite_texts(row)
    return save_book(data_dir, book)


def table_rows(book: dict) -> list[dict]:
    trades = list(book.get("trades") or [])
    for row in trades:
        _heal_incomplete_close(row)
        _refresh_open_pnl(row)
        _rewrite_texts(row)
    trades.sort(key=lambda t: t.get("opened_at") or "", reverse=True)
    return trades
