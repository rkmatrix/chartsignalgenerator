from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from pa.babysitter.advise import advise
from pa.open_session.contract import texts_from_contract
from pa.open_session.grade import prediction_for, score_for, verdict_for

ET = ZoneInfo("America/New_York")
log = logging.getLogger("pa.ledger")

# A quote failure and "no quote exists" both arrive as None, so a dead feed looks
# exactly like a quiet one. If the NBBO goes down mid-session the desk stops
# marking positions and no exit can fire, silently, which is the worst way for
# this to fail. Count the misses and say so.
QUOTE_DARK_AFTER = 20
_quote_misses = 0


def quote_health() -> int:
    """Consecutive failed quote refreshes. 0 means the feed is answering."""
    return _quote_misses


def _note_quote(ok: bool) -> None:
    global _quote_misses
    if ok:
        if _quote_misses >= QUOTE_DARK_AFTER:
            log.warning("quote feed answering again after %d misses", _quote_misses)
        _quote_misses = 0
        return
    _quote_misses += 1
    if _quote_misses == QUOTE_DARK_AFTER:
        log.error(
            "quote feed dark: %d consecutive misses; positions are not being "
            "marked and exits cannot fire",
            _quote_misses,
        )


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


# save_book is a read-modify-write, and the scan loop, the advisory refresh and
# the API all call it. Serialising them is what makes "merge then replace" safe;
# without it two savers interleave and one set of updates is silently dropped.
_BOOK_LOCK = threading.Lock()


def save_book(data_dir: Path, book: dict) -> dict:
    """Persist the tape. Incoming rows win by id; missing ids from disk/bak are kept."""
    path = book_path(data_dir)
    bak = _bak_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _BOOK_LOCK:
        merged: dict[str, dict] = {}
        for src in (_trades_from_file(bak), _trades_from_file(path), list(book.get("trades") or [])):
            for row in src:
                tid = row.get("id")
                if tid:
                    merged[str(tid)] = row
        book = {"trades": list(merged.values())}
        payload = json.dumps(book, default=str, indent=2)
        # A per-call temp name, so a saver from another process cannot rename our
        # file out from under us and leave the write lost to WinError 2.
        tmp = path.with_name(f"signal_book.json.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            if path.exists():
                try:
                    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
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


def carries_fade(sig: dict) -> bool:
    """A failed opening-range break is exempt from the one-side-per-name rule.

    The fade only fires when the earlier side is being proven wrong, so refusing
    it because that side is already on is backwards. Replayed across every
    ticker: with the rule applied the fade produced 0 TAKEs and -0.51% of summed
    move, exempt it produced 11 TAKEs at 55% and +1.17%, and no other signal
    changed (160 of them, -2.40%, in both runs).
    """
    return "orb_fade" in [str(s) for s in (sig.get("strategies") or [])]


def other_side_block(ticker: str, direction: str, taken: dict[str, str]) -> str | None:
    ticker = ticker.upper()
    direction = str(direction or "").lower()
    prior = taken.get(ticker)
    if prior and prior != direction:
        return f"{ticker} already {prior} today — not taking the other side"
    return None


# These names move together closely enough that holding several of them on the
# same side is one position, not several.
INDEX_NAMES = frozenset({"SPY", "SPX", "QQQ", "DIA", "IWM"})
# Both tails of the premium range lose, so the desk trades the middle of it.
# Measured over the 58 trades priced on the live feed (older rows came off the
# delayed chain and cannot settle this): sub-$1.00 contracts won 28.6% and lost
# $135, and $6.00+ contracts lost $461 across four trades, while $1.00-$3.50 won
# 50.0% and roughly broke even. Cheap contracts lose because the round trip is a
# large share of the premium; expensive ones lose because one bad fill outweighs
# several good ones. Holding the band turns -$815 into -$67 over that sample,
# improves both halves of it independently, and beats the unfiltered book in
# 96.1% of 4,000 bootstrap resamples.
MIN_PREMIUM = 1.00
MAX_PREMIUM = 3.50
MAX_RISK_PER_TRADE = 225.0
MAX_OPEN_PER_SIDE = 3
MAX_INDEX_PER_SIDE = 1


def risk_block(sig: dict) -> str | None:
    """Refuse a contract that is too expensive to risk, or too cheap to be real.

    Every row is one contract, so risk per trade is whatever the premium happens
    to be. On 2026-09-11 that made one SPX contract ($1,190) fifty times the
    risk of one NFLX contract ($24), and SPX alone was 56% of the day's loss.
    Cheap contracts are refused from the other end: their spread is wider than
    any edge, so their percentage P&L is mostly noise.
    """
    entry = sig.get("entry")
    if entry is None:
        return None
    entry = float(entry)
    if entry < MIN_PREMIUM:
        return f"premium ${entry:.2f} below ${MIN_PREMIUM:.2f} — spread is wider than the edge"
    if entry > MAX_PREMIUM:
        return f"premium ${entry:.2f} over ${MAX_PREMIUM:.2f} — one bad fill outweighs several good ones"
    stop = float(sig.get("plan_stop_pct") or 25.0)
    risk = entry * 100.0 * stop / 100.0
    if risk > MAX_RISK_PER_TRADE:
        return f"one contract risks ${risk:.0f}, over the ${MAX_RISK_PER_TRADE:.0f} cap"
    return None


# A day this far down stops trading. Deliberately loose: it is not tuned to lift
# P&L and the four live-feed sessions available cannot show that it would. It
# bounds the tail instead. On 2026-09-11 the desk kept opening positions into a
# losing tape and finished -$932 unfiltered, -$332 under the premium band; a
# stop this size ends that session early rather than letting it run.
#
# Its mirror, a profit lock, is deliberately absent: locking at +$25 raised the
# share of green sessions to 91% while turning -$67 into -$149 over the same
# trades, because it cuts winning days short and leaves losing ones whole.
MAX_DAILY_LOSS = 300.0


def daily_loss_block(trades: list[dict], day: str) -> str | None:
    """Refuse new entries once the session's realised loss passes the stop."""
    realised = sum(
        float(t.get("pnl_dollars") or 0.0)
        for t in trades
        if t.get("status") == "closed" and str(t.get("opened_at") or "")[:10] == day
    )
    if realised <= -MAX_DAILY_LOSS:
        return f"day is down ${-realised:.0f}, past the ${MAX_DAILY_LOSS:.0f} stop"
    return None


def crowding_block(sig: dict, trades: list[dict], day: str) -> str | None:
    """Stop the desk stacking the same macro bet under different tickers.

    Between 09:50 and 10:00 on 2026-09-11 it opened calls on AAPL, AMZN, MSFT,
    QQQ, DIA and SPX at once. That is not six ideas, it is one long-beta bet at
    six times size, and it lost $769 together when the tape ticked down.
    """
    side = str(sig.get("direction") or "").lower()
    live = [
        t
        for t in trades
        if t.get("status") == "open"
        and _row_session_day(t) == day
        and str(t.get("direction") or "").lower() == side
    ]
    if len(live) >= MAX_OPEN_PER_SIDE:
        return f"{len(live)} {side}s already open — capped at {MAX_OPEN_PER_SIDE} a side"
    if str(sig.get("ticker") or "").upper() in INDEX_NAMES:
        held = sum(1 for t in live if str(t.get("ticker") or "").upper() in INDEX_NAMES)
        if held >= MAX_INDEX_PER_SIDE:
            return f"already holding an index {side} — SPY/SPX/QQQ/DIA/IWM are one trade"
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
# Long enough to discard a bad first print, short enough that a 0DTE contract
# cannot travel from -25% to -65% inside the grace period.
STOP_GRACE_MINUTES = 1
QUOTE_DEATH_MINUTES = 5
QUOTE_DEATH_PCT = -50.0
SELL_RETRY_MINUTES = 10
# Entries get the same second chance exits already had. A restart between the
# moment a row is written and the moment Telegram accepts the Buy used to drop
# that alert silently, because nothing recorded that it was still owed.
ENTRY_RETRY_MINUTES = 15


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
    if row.get("prediction") == "unknown":
        return
    pct = row.get("pnl_pct")
    if pct is None:
        return
    row["prediction"] = prediction_for(row.get("verdict"), float(pct))


def _resolve_exit(row: dict, action: str) -> float | None:
    """Sell fill is the live bid — never the mid, and never a modeled stop/TP target.

    The mark is the NBBO mid so the dashboard reads like a broker, but a market
    sell lifts the bid, so that is what gets booked as the realized exit.
    """
    mark = row.get("bid") or row.get("mark")
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


def _recently_opened(row: dict, now: datetime) -> bool:
    raw = row.get("opened_at")
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
    return max(0.0, (now - ts).total_seconds() / 60.0) <= ENTRY_RETRY_MINUTES


def _backfill_entry_alerts(trades: list[dict], day: str) -> None:
    """Treat every row from a previous session as already announced.

    telegram_sent is new, so nothing written before it exists carries the flag.
    Without this the retry sweep would read a whole book of unflagged history as
    a backlog of unsent Buys and fire all of it at once. Only rows opened today
    are left unflagged, because they are the only ones an alert could still be
    owed for.
    """
    for row in trades:
        if "telegram_sent" in row:
            continue
        if str(row.get("opened_at") or "")[:10] != day:
            row["telegram_sent"] = True


def _sanitize_mark(row: dict, held: float) -> None:
    """A 0–5m −50% to −99% print is a quote death, not a fill. Do not mark or sell it.

    This guard exists for the delayed Yahoo chain, which prints garbage ticks.
    A real-time NBBO that says −50% means the option actually fell 50%, which
    0DTE does routinely, so suppressing it would just hide real losses.
    """
    if row.get("quote_source") == "uw":
        return
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


def _expire_stale(row: dict, now: datetime) -> bool:
    """Close a row whose contract already expired.

    Without this an option that expired days ago sits 'open' forever: never
    graded, never sold, and still counted as a live position.
    """
    if row.get("status") == "closed":
        return False
    raw = str(row.get("expiry") or "")[:10]
    if not raw:
        return False
    try:
        expiry = date.fromisoformat(raw)
    except ValueError:
        return False
    today = (now.astimezone(ET) if now.tzinfo else now).date()
    if expiry >= today:
        return False
    had_quote = bool(row.get("quoted")) and row.get("bid") is not None
    exit_px = row.get("bid") or row.get("mark") or row.get("entry")
    row["status"] = "closed"
    row["closed_at"] = now.isoformat()
    row["exit"] = None if exit_px is None else round(float(exit_px), 2)
    dollars, pct = _pnl(row.get("entry"), row.get("exit"))
    row["pnl_dollars"] = dollars
    row["pnl_pct"] = pct
    if had_quote:
        row["reason"] = "expired"
        row["headline"] = f"Contract expired {raw} — closed at the last live bid."
        _apply_prediction(row)
    else:
        # No live quote ever landed, so the outcome is unknowable. Booking it at
        # the entry would hand the stats a free 0% PASS; leave it ungraded.
        row["reason"] = "expired_unquoted"
        row["headline"] = f"Contract expired {raw} with no live quote — not graded."
        row["prediction"] = "unknown"
    return True


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
    # The low-water mark is what tells us whether the breakeven floor scratched a
    # trade that would have come back. Without it that cost is unmeasurable.
    trough = row.get("trough_mark")
    if trough is None or float(mark) < float(trough):
        row["trough_mark"] = float(mark)
    _record_path(row, mark)


# Endpoints plus two extremes cannot answer a single exit question. Whether a
# lower breakeven arm would have rescued QQQ (+6.4% peak, -16.7% close) or
# scratched NVDA (+51.3% peak) depends on whether NVDA dipped past the arm on its
# way up, and nothing in the book records that. Keep the series so exit rules can
# be replayed against what actually happened instead of guessed at.
PATH_MAX_POINTS = 600


def _record_path(row: dict, mark) -> None:
    """Append this poll's quote to the trade's own price history."""
    try:
        path = row.get("marks")
        if not isinstance(path, list):
            path = []
        bid = row.get("bid")
        point = [
            datetime.now(ET).strftime("%H:%M:%S"),
            round(float(mark), 4),
            round(float(bid), 4) if bid is not None else None,
        ]
        # A flat quote repeated for an hour teaches nothing and bloats the file.
        if path and path[-1][1] == point[1] and path[-1][2] == point[2]:
            return
        path.append(point)
        row["marks"] = path[-PATH_MAX_POINTS:]
    except Exception:
        # Instrumentation must never be able to interfere with a position.
        pass


def _quote_row(row: dict, settings=None) -> float | None:
    """Refresh the mark from the live NBBO and remember what we could sell at."""
    if row.get("strike") is None or not row.get("expiry") or not row.get("ticker"):
        return None
    try:
        from pa.open_session.contract import quote_detail

        hit = quote_detail(
            str(row["ticker"]),
            float(row["strike"]),
            row.get("expiry"),
            str(row.get("direction") or row.get("opt") or ""),
            fetch=True,
            settings=settings,
        )
    except Exception:
        _note_quote(False)
        return None
    if not hit:
        _note_quote(False)
        return None
    px = hit.get("mid") or hit.get("bid") or hit.get("ask")
    if px is None:
        _note_quote(False)
        return None
    _note_quote(True)
    row["mark"] = round(float(px), 2)
    row["bid"] = hit.get("bid")
    row["quote_source"] = hit.get("source")
    row["quote_age"] = None if hit.get("age") is None else round(float(hit["age"]), 1)
    row["quoted"] = True
    return row["mark"]


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
        _expire_stale(row, now)
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
        if not carries_fade(sig):
            blocked = other_side_block(
                str(sig.get("ticker") or ""), str(sig.get("direction") or ""), taken
            )
            if blocked:
                continue
        if risk_block(sig):
            continue
        if crowding_block(sig, trades, day):
            continue
        if daily_loss_block(trades, day):
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
            _quote_row(row, settings)
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
            # _resolve_exit fills at the bid, so the floor has to judge on it too.
            bid=row.get("bid"),
        )
        if advice.action not in {"HARD_SELL", "STRONG_SELL", "TAKE_PROFIT"}:
            if (
                minutes_to_close is not None
                and minutes_to_close <= 20
                and str(row.get("expiry") or "")[:10] == day
            ):
                if quote_exit:
                    _quote_row(row, settings)
                    _sanitize_mark(row, held)
                _close(row, now, exit_px=_resolve_exit(row, "HARD_SELL"), reason="time_stop", settings=settings)
                closed_now = True
            continue
        headline_l = (advice.headline or "").lower()
        is_tape_flip = "tape flipped" in headline_l
        is_breakout = ("breakout" in headline_l or "thesis broken" in headline_l) and not is_tape_flip
        is_plan = "plan stop" in headline_l
        # A plan stop is a risk limit, not a suggestion. Holding it back for
        # PLAN_HOLD_MINUTES turned it into a 15-minute timer: on 2026-09-11 all
        # eight stop-outs blew straight through -25% and were dumped between
        # -30% and -65% the moment the timer expired, costing ~$352 more than
        # the plan allowed. Only a one-minute grace survives, so we never act on
        # the first quote after a fill.
        if is_plan:
            if held < STOP_GRACE_MINUTES:
                continue
        else:
            if is_breakout and held < BREAKOUT_HOLD_MINUTES:
                continue
            if held < HOLD_MINUTES and advice.action in {"HARD_SELL", "STRONG_SELL"} and not is_tape_flip:
                # First 5 minutes: no SL, no trigger. Quote deaths rejected above.
                continue
        reason = {
            "HARD_SELL": "underlying_stop" if "through SL" in (advice.headline or "") else "hard_stop",
            "STRONG_SELL": "failed_breakout" if "breakout" in (advice.headline or "").lower() else "thesis_broken",
            "TAKE_PROFIT": "take_profit",
        }.get(advice.action, advice.action.lower())
        if quote_exit:
            _quote_row(row, settings)
        _close(
            row,
            now,
            exit_px=_resolve_exit(row, advice.action),
            reason=reason,
            headline=advice.headline,
            settings=settings,
        )
        closed_now = True

    _backfill_entry_alerts(trades, day)
    if settings is not None:
        for row in trades:
            if row.get("telegram_sold") or not _is_sold(row) or not _recently_closed(row, now):
                continue
            # notify_sell owns the verdict policy so TAKE/WATCH is decided in one place.
            _maybe_telegram_sell(row, settings)
        # An entry whose Buy never reached Telegram is still owed one, as long as
        # it is recent enough to act on. notify_take is idempotent and owns the
        # verdict policy, so a row that was already announced is a no-op here.
        for row in trades:
            if row.get("telegram_sent") or not _recently_opened(row, now):
                continue
            try:
                from pa.open_session.telegram import notify_take

                notify_take(row, settings=settings)
            except Exception:
                pass

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
    row["headline"] = headline
    row["exit"] = round(float(exit_px), 2)
    dollars, pct = _pnl(row.get("entry"), row["exit"])
    row["pnl_dollars"] = dollars
    row["pnl_pct"] = pct
    # The advisor decides on the mark but the fill is the bid, so a "take profit"
    # can land red on a wide 0DTE spread. Calling that a take-profit misreports
    # the day and teaches the policy that a losing exit was a good one.
    if reason == "take_profit" and pct is not None and float(pct) <= 0:
        reason = "trail_stop"
    row["reason"] = reason
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
