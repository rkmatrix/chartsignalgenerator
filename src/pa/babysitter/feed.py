from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from urllib.request import urlopen

from pa.babysitter.advise import Advice, advise
from pa.config import Settings
from pa.execution.paper import PaperBroker


def fetch_dosv_positions(url: str, timeout: float = 2.0) -> list[dict]:
    try:
        with urlopen(url.rstrip("/") + "/api/positions", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    return list(body.get("positions") or [])


def load_watches(data_dir: Path) -> list[dict]:
    path = data_dir / "history" / "watched_positions.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_watch(data_dir: Path, row: dict) -> list[dict]:
    rows = load_watches(data_dir)
    key = _watch_key(row)
    row["key"] = key
    rows = [r for r in rows if r.get("key") != key] + [row]
    path = data_dir / "history" / "watched_positions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows


def _watch_key(row: dict) -> str:
    right = str(row.get("right") or row.get("option_type") or "call")
    return f"{row.get('ticker')}-{right}-{row.get('strike')}-{row.get('expiry')}"


def _lv_get(lv, name: str):
    if lv is None:
        return None
    if isinstance(lv, dict):
        return lv.get(name)
    return getattr(lv, name, None)


def _is_call(raw: dict) -> bool:
    token = str(raw.get("option_type") or raw.get("type") or raw.get("right") or "call")
    return token.lower().startswith("c")


def _dte(expiry, today: date | None = None) -> int | None:
    if not expiry:
        return None
    today = today or date.today()
    try:
        exp = datetime.fromisoformat(str(expiry)[:10]).date()
    except Exception:
        return None
    return (exp - today).days


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _label(ticker: str, is_call: bool, strike, expiry) -> str:
    right = "C" if is_call else "P"
    strike_s = f"{float(strike):g}" if strike not in (None, "") else ""
    exp = str(expiry)[:10] if expiry else ""
    return " ".join(p for p in (ticker, f"{strike_s}{right}" if strike_s else right, exp) if p)


def load_open_plans(data_dir: Path) -> dict[str, dict]:
    path = data_dir / "history" / "strongest.json"
    if not path.exists():
        return {}
    try:
        tape = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for sig in tape.get("signals") or []:
        ticker = str(sig.get("ticker") or "").upper()
        if ticker:
            out[ticker] = sig
    strongest = tape.get("strongest") or {}
    if strongest.get("ticker") and strongest["ticker"] not in out:
        out[str(strongest["ticker"]).upper()] = strongest
    return out


def _seed_spy_watch(settings: Settings) -> None:
    if load_watches(settings.data_dir):
        return
    monday_path = settings.data_dir / "history" / "monday_signals.json"
    if not monday_path.exists():
        return
    try:
        monday = json.loads(monday_path.read_text(encoding="utf-8"))
    except Exception:
        return
    cards = [c for c in (monday.get("cards") or []) if c.get("ticker") == "SPY"]
    card = next((c for c in cards if c.get("strike") == 770), cards[0] if cards else None)
    if not card:
        return
    save_watch(
        settings.data_dir,
        {
            "ticker": "SPY",
            "right": card.get("right") or "call",
            "strike": card.get("strike"),
            "expiry": card.get("expiry"),
            "entry": card.get("entry"),
            "text_buy": card.get("text_buy"),
            "note": "operator card — babysit until structure breaks or the clock says flatten",
            "plan_stop_pct": 25.0,
        },
    )


def _plan_kwargs(ticker: str, watch: dict | None, plans: dict[str, dict]) -> dict:
    plan = plans.get(ticker) or {}
    watch = watch or {}
    stop = _float(watch.get("stop") or watch.get("underlying_stop") or plan.get("stop"))
    trigger = _float(watch.get("trigger") or plan.get("trigger"))
    plan_stop = _float(watch.get("plan_stop_pct") or plan.get("plan_stop_pct"))
    tape = plan.get("direction") if plan.get("direction") in {"call", "put"} else None
    return {
        "plan_stop_pct": plan_stop,
        "underlying_stop": stop,
        "trigger": trigger,
        "tape_direction": tape,
    }


def review_positions(
    settings: Settings,
    broker: PaperBroker | None = None,
    levels_by_ticker: dict | None = None,
    dosv_url: str = "",
    minutes_to_close: int | None = None,
    today: date | None = None,
) -> dict:
    levels_by_ticker = levels_by_ticker or {}
    today = today or date.today()
    _seed_spy_watch(settings)
    plans = load_open_plans(settings.data_dir)
    rows: list[dict] = []
    seen: set[str] = set()

    for raw in fetch_dosv_positions(dosv_url) if dosv_url else []:
        ticker = str(raw.get("ticker") or raw.get("underlying") or raw.get("symbol") or "").upper()
        if not ticker:
            continue
        is_call = _is_call(raw)
        strike = raw.get("strike")
        expiry = raw.get("expiry")
        entry = _float(raw.get("avg_entry") or raw.get("average_price") or raw.get("entry")) or 0.01
        mark = _float(raw.get("mark") or raw.get("last_mark") or raw.get("last"))
        lv = levels_by_ticker.get(ticker)
        dte = _dte(expiry, today)
        dosv_reco = raw.get("reco") if isinstance(raw.get("reco"), dict) else {}
        user_hold = str(raw.get("override") or "").lower() == "hold" or "as requested" in str(
            dosv_reco.get("headline") or ""
        ).lower()
        if user_hold:
            advice = Advice(
                "HOLD",
                1.0,
                dosv_reco.get("headline") or "Holding as you requested — SignalValidator override",
                list(dosv_reco.get("reasons") or ["user hold"]),
            )
        else:
            advice = advise(
                is_call=is_call,
                entry=entry,
                mark=mark,
                underlying=_lv_get(lv, "last") or _float(raw.get("underlying_price")),
                vwap=_lv_get(lv, "vwap"),
                ema9=_lv_get(lv, "ema9"),
                ema9_prev=_lv_get(lv, "ema9_prev"),
                minutes_to_close=minutes_to_close,
                is_0dte=dte == 0,
                peak_mark=_float(raw.get("peak_mark")),
                bid=_float(raw.get("bid")),
                days_to_expiry=dte,
                **_plan_kwargs(ticker, None, plans),
            )
        key = f"{ticker}|{strike}|{'C' if is_call else 'P'}"
        seen.add(key)
        rows.append(
            {
                "source": "robinhood",
                "ticker": ticker,
                "label": raw.get("contract_label") or _label(ticker, is_call, strike, expiry),
                "strike": strike,
                "expiry": expiry,
                "pl_pct": raw.get("pl_pct"),
                "dosv_action": dosv_reco.get("action"),
                **advice.as_dict(),
            }
        )

    for watch in load_watches(settings.data_dir):
        ticker = str(watch.get("ticker") or "").upper()
        is_call = _is_call(watch)
        strike = watch.get("strike")
        key = f"{ticker}|{strike}|{'C' if is_call else 'P'}"
        if key in seen:
            continue
        lv = levels_by_ticker.get(ticker)
        mark = _float(watch.get("mark"))
        expiry = watch.get("expiry")
        dte = _dte(expiry, today)
        advice = advise(
            is_call=is_call,
            entry=float(watch.get("entry") or 0),
            mark=mark,
            underlying=_lv_get(lv, "last"),
            vwap=_lv_get(lv, "vwap"),
            ema9=_lv_get(lv, "ema9"),
            ema9_prev=_lv_get(lv, "ema9_prev"),
            minutes_to_close=minutes_to_close,
            is_0dte=dte == 0,
            days_to_expiry=dte,
            **_plan_kwargs(ticker, watch, plans),
        )
        rows.append(
            {
                "source": "watch",
                "ticker": ticker,
                "label": _label(ticker, is_call, strike, expiry),
                "strike": strike,
                "expiry": expiry,
                "contract": watch,
                **advice.as_dict(),
            }
        )

    if broker is not None:
        for pos in broker.snapshot():
            if pos.qty == 0:
                continue
            ticker = pos.ticker
            lv = levels_by_ticker.get(ticker)
            is_call = "C" in (pos.occ_symbol or "")
            qty = pos.qty * pos.multiplier
            mark = pos.avg_price
            if qty:
                mark = pos.avg_price + pos.unrealized_pnl / qty
            advice = advise(
                is_call=is_call if pos.occ_symbol else True,
                entry=pos.avg_price,
                mark=mark,
                underlying=_lv_get(lv, "last"),
                vwap=_lv_get(lv, "vwap"),
                ema9=_lv_get(lv, "ema9"),
                ema9_prev=_lv_get(lv, "ema9_prev"),
                minutes_to_close=minutes_to_close,
                **_plan_kwargs(ticker, None, plans),
            )
            rows.append({"source": "paper", "ticker": ticker, "label": pos.occ_symbol or ticker, "qty": pos.qty, **advice.as_dict()})

    payload = {
        "ok": True,
        "note": "Advice only. PA does not place Robinhood orders. Auto-sell stays in SignalValidator and is off unless you enable it there.",
        "positions": rows,
    }
    path = settings.data_dir / "history" / "babysitter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    return payload
