"""Paper-tape learning: each FAIL retunes the next hunt. Not ML, not 99%.

Closed P&L from the signal book retunes the next print immediately:
  * exact recipe (name/side/setups/window)
  * same stack+window+side on any ticker (META orb-call FAIL blocks GOOGL)
  * same session window+side after an ugly loss (first-hour calls dying)
  * session halt after a 2-FAIL streak or sub-40% morning
We never raise size from a hot streak.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from statistics import mean

from pa.chart_trader.monday import wilson_interval
from pa.open_session.setups import Candidate

WINDOW = 20
MIN_N_SKIP = 2
SKIP_STREAK = 2
SKIP_AVG_PCT = -5.0
EXTRA_AVG_PCT = -10.0
WEIGHT_FLOOR = 0.55
UGLY_PCT = -15.0
SESSION_HALT_STREAK = 2
SESSION_HALT_N = 3
SESSION_HALT_WR = 0.4


def learn_path(data_dir: Path) -> Path:
    return data_dir / "history" / "open_learn.json"


def _empty() -> dict:
    return {
        "n": 0,
        "wins": 0,
        "tickers": {},
        "pairs": {},
        "strategies": {},
        "lessons": [],
        "skipped": [],
        "fingerprints": {},
        "patterns": {},
        "window_sides": {},
        "session": {"n": 0, "passes": 0, "streak": 0, "halt": False, "why": ""},
        "policy": {},
        "autopsy": [],
        "blocked": [],
        "accuracy": {"take": {"n": 0, "passes": 0}, "watch": {"n": 0, "passes": 0}, "skip": {"n": 0, "passes": 0}},
        "cutoffs": {"take": 65, "watch": 50},
    }


def _bucket() -> dict:
    return {
        "n": 0,
        "wins": 0,
        "pnl_pcts": [],
        "pnl_dollars": 0.0,
        "streak": 0,
        "weight": 1.0,
        "skip": False,
        "require_extra": False,
        "why": "",
        "closes": [],
    }


def load_state(data_dir: Path) -> dict:
    path = learn_path(data_dir)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    data.setdefault("tickers", {})
    data.setdefault("pairs", {})
    data.setdefault("strategies", {})
    data.setdefault("lessons", [])
    data.setdefault("skipped", [])
    data.setdefault("fingerprints", {})
    data.setdefault("patterns", {})
    data.setdefault("window_sides", {})
    data.setdefault("session", {"n": 0, "passes": 0, "streak": 0, "halt": False, "why": ""})
    data.setdefault("autopsy", [])
    data.setdefault("blocked", [])
    data.setdefault("accuracy", {"take": {"n": 0, "passes": 0}, "watch": {"n": 0, "passes": 0}, "skip": {"n": 0, "passes": 0}})
    data.setdefault("cutoffs", {"take": 65, "watch": 50})
    return data


def save_state(data_dir: Path, state: dict) -> dict:
    path = learn_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")
    return state


def _ingest(bucket: dict, pnl_pct: float, pnl_dollars: float, won: bool, day: str = "", pred: str = "") -> None:
    bucket["n"] = int(bucket.get("n") or 0) + 1
    if won:
        bucket["wins"] = int(bucket.get("wins") or 0) + 1
        bucket["streak"] = max(1, int(bucket.get("streak") or 0) + 1) if int(bucket.get("streak") or 0) > 0 else 1
    else:
        bucket["streak"] = min(-1, int(bucket.get("streak") or 0) - 1) if int(bucket.get("streak") or 0) < 0 else -1
    pcts = list(bucket.get("pnl_pcts") or [])
    pcts.append(float(pnl_pct))
    bucket["pnl_pcts"] = pcts[-WINDOW:]
    bucket["pnl_dollars"] = round(float(bucket.get("pnl_dollars") or 0) + float(pnl_dollars), 2)
    closes = list(bucket.get("closes") or [])
    rec = {"day": day, "won": won, "pct": float(pnl_pct)}
    if pred:
        rec["pred"] = pred
    closes.append(rec)
    bucket["closes"] = closes[-WINDOW:]


def _finalize(bucket: dict, *, session_day: str | None = None) -> None:
    n = int(bucket.get("n") or 0)
    wins = int(bucket.get("wins") or 0)
    pcts = [float(x) for x in (bucket.get("pnl_pcts") or [])]
    avg = mean(pcts) if pcts else 0.0
    wr = wins / n if n else 0.0
    lo, _, _ = wilson_interval(wins, n) if n else (0.0, 0.0, 0.0)
    if n < 2:
        weight = 1.0
    elif avg >= 0:
        weight = 1.0
    elif avg >= SKIP_AVG_PCT:
        weight = 0.8
    else:
        weight = WEIGHT_FLOOR
    bucket["avg_pnl_pct"] = round(avg, 1)
    bucket["win_rate"] = round(wr, 3)
    bucket["wilson_lo"] = round(lo, 3)
    bucket["weight"] = weight
    closes = list(bucket.get("closes") or [])
    last = closes[-SKIP_STREAK:]
    last_avg = mean(float(c["pct"]) for c in last) if last else 0.0
    last_all_lost = len(last) >= SKIP_STREAK and all(not c.get("won") for c in last)
    today_closes = [c for c in closes if c.get("day") == session_day]
    today_avg = mean(float(c["pct"]) for c in today_closes) if today_closes else 0.0
    today_loss_streak = 0
    for c in reversed(today_closes):
        if c.get("won"):
            break
        today_loss_streak += 1
    skip_hist = last_all_lost and last_avg < SKIP_AVG_PCT
    skip_today = (
        len(today_closes) >= MIN_N_SKIP
        and today_loss_streak >= SKIP_STREAK
        and today_avg < SKIP_AVG_PCT
    )
    skip = skip_hist or skip_today
    extra = (n >= MIN_N_SKIP and avg <= EXTRA_AVG_PCT) or (n >= MIN_N_SKIP and wr < 0.4 and avg < 0)
    bucket["skip"] = skip
    bucket["require_extra"] = extra and not skip
    if skip:
        n_lost = today_loss_streak if skip_today else SKIP_STREAK
        shown = today_avg if skip_today else last_avg
        bucket["why"] = (
            f"{n_lost} losses in a row, avg {shown:.1f}% — skip this name/side today"
        )
    elif extra:
        bucket["why"] = f"{n} closes, avg {avg:.1f}% — need a third family before we take it again"
    else:
        bucket["why"] = ""


def stack_key(strategies: list | None) -> str:
    return "+".join(sorted({str(s) for s in (strategies or []) if s}))


def _row_window(row: dict) -> str:
    raw = row.get("window")
    if raw:
        return str(raw)
    ts = row.get("opened_at")
    if not ts:
        return ""
    try:
        opened = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return ""
    from pa.open_session.clock import minutes_since_open
    from pa.open_session.playbook import session_window

    return session_window(minutes_since_open(opened)) or ""


def fingerprint_key(ticker: str, direction: str, strategies: list | None, window: str = "") -> str | None:
    stack = stack_key(strategies)
    if not ticker or not direction or not stack:
        return None
    parts = [ticker.upper(), direction.lower(), stack]
    if window:
        parts.append(window)
    return "|".join(parts)


def pattern_key(strategies: list | None, window: str, direction: str) -> str | None:
    stack = stack_key(strategies)
    window = str(window or "")
    direction = str(direction or "").lower()
    if not stack or not window or direction not in {"call", "put"}:
        return None
    return f"{stack}|{window}|{direction}"


def window_side_key(window: str, direction: str) -> str | None:
    window = str(window or "")
    direction = str(direction or "").lower()
    if not window or direction not in {"call", "put"}:
        return None
    return f"{window}|{direction}"


def _fp_failed(close: dict | None) -> bool:
    if not close:
        return False
    pred = close.get("pred")
    if pred:
        return pred == "FAIL"
    return not close.get("won")


def _finalize_fp(bucket: dict, *, session_day: str, label: str) -> None:
    closes = list(bucket.get("closes") or [])
    streak = 0
    for c in reversed(closes):
        if not _fp_failed(c):
            break
        streak += 1
    last = closes[-1] if closes else None
    last_day = str(last.get("day") or "") if last else ""
    last_pct = float(last.get("pct") or 0) if last else 0.0
    skip_today = bool(last and _fp_failed(last) and last_day == session_day)
    skip_streak = streak >= SKIP_STREAK
    bucket["skip"] = skip_today or skip_streak
    if skip_today:
        bucket["why"] = (
            f"FAIL {label} today ({last_pct:+.1f}%) — not repeating that recipe"
        )
    elif skip_streak:
        bucket["why"] = f"{streak} FAILs on {label} — sit out this recipe"
    else:
        bucket["why"] = ""


def _finalize_pattern(bucket: dict, *, session_day: str, label: str) -> None:
    """Same stack+window+side on any name — META FAIL today blocks GOOGL's copy.

    Cross-day streaks stay on the ticker fingerprint so one QQQ loss yesterday
    does not sit out every name running that stack tomorrow.
    """
    closes = list(bucket.get("closes") or [])
    last = closes[-1] if closes else None
    last_day = str(last.get("day") or "") if last else ""
    last_pct = float(last.get("pct") or 0) if last else 0.0
    skip_today = bool(last and _fp_failed(last) and last_day == session_day)
    bucket["skip"] = skip_today
    if skip_today:
        bucket["why"] = (
            f"FAIL {label} today ({last_pct:+.1f}%) — not repeating that stack this session"
        )
    else:
        bucket["why"] = ""


def _finalize_window_side(bucket: dict, *, session_day: str, label: str) -> None:
    today = [c for c in (bucket.get("closes") or []) if c.get("day") == session_day]
    fails = [c for c in today if _fp_failed(c)]
    n = len(today)
    wr = (n - len(fails)) / n if n else 1.0
    avg = mean(float(c.get("pct") or 0) for c in today) if today else 0.0
    ugly = any(float(c.get("pct") or 0) <= UGLY_PCT and _fp_failed(c) for c in today)
    skip = bool(ugly) or (len(fails) >= 2 and avg <= -10.0) or (n >= 3 and wr < 0.4)
    bucket["skip"] = skip
    if ugly:
        worst = min(float(c.get("pct") or 0) for c in fails) if fails else 0.0
        bucket["why"] = f"Ugly {label} FAIL today ({worst:+.1f}%) — sitting out that window/side"
    elif skip and len(fails) >= 2:
        bucket["why"] = f"{len(fails)} FAILs on {label} today (avg {avg:.1f}%) — sitting it out"
    elif skip:
        bucket["why"] = f"{label} is {n - len(fails)}/{n} today — sitting out that window/side"
    else:
        bucket["why"] = ""


def _session_from_closes(closes: list[dict], session_day: str) -> dict:
    today = [c for c in closes if c.get("day") == session_day]
    n = len(today)
    passes = sum(1 for c in today if not _fp_failed(c))
    streak = 0
    ugly_streak = 0
    for c in reversed(today):
        if not _fp_failed(c):
            break
        streak += 1
        if float(c.get("pct") or 0) > UGLY_PCT:
            break
        ugly_streak += 1
    wr = passes / n if n else 1.0
    halt = ugly_streak >= SESSION_HALT_STREAK or (n >= SESSION_HALT_N and wr < SESSION_HALT_WR)
    why = ""
    if ugly_streak >= SESSION_HALT_STREAK:
        why = f"{ugly_streak} ugly TAKE FAILs in a row today — no new TAKEs until the tape proves itself"
    elif n >= SESSION_HALT_N and wr < SESSION_HALT_WR:
        why = f"Today {passes}/{n} TAKE PASS ({round(wr * 100)}%) — halt new TAKEs"
    return {"n": n, "passes": passes, "streak": streak, "halt": halt, "why": why}


def rebuild(data_dir: Path, today: date | None = None) -> dict:
    from pa.open_session.grade import prediction_for, tune_take_cut, WATCH_CUT_DEFAULT
    from pa.open_session.ledger import load_book

    today = today or date.today()
    day = today.isoformat()
    state = _empty()
    acc = {
        "TAKE": {"n": 0, "passes": 0},
        "WATCH": {"n": 0, "passes": 0},
        "SKIP": {"n": 0, "passes": 0},
    }
    take_closes: list[dict] = []
    rows = sorted(load_book(data_dir).get("trades") or [], key=lambda r: str(r.get("closed_at") or r.get("opened_at") or ""))
    for row in rows:
        if row.get("status") != "closed" or row.get("exit") is None:
            continue
        pnl_pct = row.get("pnl_pct")
        pnl_d = row.get("pnl_dollars")
        if pnl_pct is None or pnl_d is None:
            continue
        won = float(pnl_d) > 0
        state["n"] += 1
        if won:
            state["wins"] += 1
        verdict = str(row.get("verdict") or "TAKE").upper()
        if verdict == "EXECUTE":
            verdict = "TAKE"
        pred = prediction_for(verdict, float(pnl_pct))
        bucket = acc.setdefault(verdict, {"n": 0, "passes": 0})
        bucket["n"] += 1
        if pred == "PASS":
            bucket["passes"] += 1
        ticker = str(row.get("ticker") or "").upper()
        direction = str(row.get("direction") or ("put" if row.get("opt") == "P" else "call")).lower()
        closed_day = str(row.get("closed_at") or "")[:10]
        strats = [str(s) for s in (row.get("strategies") or []) if s]
        window = _row_window(row)
        if ticker:
            state["tickers"].setdefault(ticker, _bucket())
            _ingest(state["tickers"][ticker], float(pnl_pct), float(pnl_d), won, closed_day)
            pair = f"{ticker}|{direction}"
            state["pairs"].setdefault(pair, _bucket())
            _ingest(state["pairs"][pair], float(pnl_pct), float(pnl_d), won, closed_day)
        for strat in strats:
            state["strategies"].setdefault(strat, _bucket())
            _ingest(state["strategies"][strat], float(pnl_pct), float(pnl_d), won, closed_day)
        printed = verdict in {"TAKE", "WATCH"}
        fp = fingerprint_key(ticker, direction, strats, window)
        if fp and printed:
            state["fingerprints"].setdefault(fp, _bucket())
            _ingest(state["fingerprints"][fp], float(pnl_pct), float(pnl_d), won, closed_day, pred=pred)
        pk = pattern_key(strats, window, direction)
        if pk and printed:
            state["patterns"].setdefault(pk, _bucket())
            _ingest(state["patterns"][pk], float(pnl_pct), float(pnl_d), won, closed_day, pred=pred)
        wk = window_side_key(window, direction)
        if wk and verdict == "TAKE":
            state["window_sides"].setdefault(wk, _bucket())
            _ingest(state["window_sides"][wk], float(pnl_pct), float(pnl_d), won, closed_day, pred=pred)
        if verdict == "TAKE":
            take_closes.append({"day": closed_day, "won": won, "pct": float(pnl_pct), "pred": pred})
        if pred == "FAIL" and printed:
            state["autopsy"].append(
                {
                    "id": row.get("id"),
                    "ticker": ticker,
                    "direction": direction,
                    "verdict": verdict,
                    "stack": stack_key(strats),
                    "window": window,
                    "reason": row.get("reason") or "",
                    "pnl_pct": float(pnl_pct),
                    "day": closed_day,
                    "correction": (
                        f"Do not TAKE {ticker} {direction} via {stack_key(strats) or 'that stack'}"
                        + (f" in {window}" if window else "")
                        + " again until it proves itself."
                    ),
                }
            )
    for rec in list(state["tickers"].values()) + list(state["pairs"].values()) + list(state["strategies"].values()):
        _finalize(rec, session_day=day)
    for key, rec in list(state["fingerprints"].items()):
        _finalize_fp(rec, session_day=day, label=key.replace("|", " "))
    for key, rec in list(state["patterns"].items()):
        _finalize_pattern(rec, session_day=day, label=key.replace("|", " "))
    for key, rec in list(state["window_sides"].items()):
        _finalize_window_side(rec, session_day=day, label=key.replace("|", " "))
    state["session"] = _session_from_closes(take_closes, day)
    state["autopsy"] = list(reversed(state["autopsy"]))[:12]
    take_n = int(acc["TAKE"]["n"])
    take_p = int(acc["TAKE"]["passes"])
    take_cut = tune_take_cut(take_p, take_n)
    state["cutoffs"] = {"take": take_cut, "watch": WATCH_CUT_DEFAULT}
    from pa.open_session.calibrate import fit_policy

    state["policy"] = fit_policy(load_book(data_dir).get("trades") or [])
    state["accuracy"] = {
        "take": {"n": take_n, "passes": take_p, "pct": round(take_p / take_n * 100) if take_n else None},
        "watch": {
            "n": acc["WATCH"]["n"],
            "passes": acc["WATCH"]["passes"],
            "pct": round(acc["WATCH"]["passes"] / acc["WATCH"]["n"] * 100) if acc["WATCH"]["n"] else None,
        },
        "skip": {
            "n": acc["SKIP"]["n"],
            "passes": acc["SKIP"]["passes"],
            "pct": round(acc["SKIP"]["passes"] / acc["SKIP"]["n"] * 100) if acc["SKIP"]["n"] else None,
        },
    }
    state["lessons"] = _lessons(state)
    state["skipped"] = [
        {"key": k, "why": v.get("why")}
        for k, v in sorted(state["pairs"].items())
        if v.get("skip")
    ]
    state["blocked"] = [
        {"key": k, "why": v.get("why")}
        for k, v in (
            list(sorted(state["fingerprints"].items()))
            + list(sorted(state["patterns"].items()))
            + list(sorted(state["window_sides"].items()))
        )
        if v.get("skip")
    ]
    state["note"] = (
        "Verdict is confidence at print (TAKE / WATCH / SKIP). "
        "Prediction is whether that call was right given option P&L. "
        "Each close retunes the next print: same stack, same window/side after an ugly FAIL, "
        "and a session halt after a 2-FAIL streak. One live TAKE at a time."
    )
    return save_state(data_dir, state)


def _lessons(state: dict) -> list[str]:
    out: list[str] = []
    n = int(state.get("n") or 0)
    wins = int(state.get("wins") or 0)
    acc = (state.get("accuracy") or {}).get("take") or {}
    take_n = int(acc.get("n") or 0)
    take_p = int(acc.get("passes") or 0)
    take_cut = int((state.get("cutoffs") or {}).get("take") or 65)
    sess = state.get("session") or {}
    pol = state.get("policy") or {}
    ins = pol.get("in_sample") or {}
    if ins.get("n"):
        hit = "at target" if ins.get("hit") else "still short of 95%"
        out.append(
            f"Calibrated TAKE slice: {ins.get('passes')}/{ins.get('n')} PASS ({ins.get('pct')}%) — {hit}."
        )
    if sess.get("halt") and sess.get("why"):
        out.append(str(sess["why"]))
    if take_n:
        pct = round(take_p / take_n * 100)
        bar = f" TAKE bar is now {take_cut} (higher = pickier)." if take_cut > 65 else ""
        out.append(f"TAKE prediction: {take_p}/{take_n} PASS ({pct}%).{bar}")
    for rec in (state.get("window_sides") or {}).values():
        why = rec.get("why") or ""
        if rec.get("skip") and why and why not in out:
            out.append(why)
    for rec in (state.get("patterns") or {}).values():
        why = rec.get("why") or ""
        if rec.get("skip") and why and why not in out:
            out.append(why)
    for item in (state.get("autopsy") or [])[:4]:
        line = item.get("correction") or ""
        if line and line not in out:
            out.append(line)
    if n:
        out.append(f"Tape so far: {wins}/{n} closed green ({wins / n * 100:.0f}%).")
    else:
        out.append("No closed paper prints yet — nothing to retune.")
    pairs = list((state.get("pairs") or {}).items())

    def _rank(kv: tuple) -> float:
        rec = kv[1]
        return abs(float(rec.get("avg_pnl_pct") or 0)) * int(rec.get("n") or 0)

    for key, rec in sorted(pairs, key=_rank, reverse=True):
        if rec.get("skip"):
            ticker, _, side = key.partition("|")
            out.append(f"Skip {ticker} {side}s today — {rec.get('why')}")
    for key, rec in sorted(pairs, key=_rank, reverse=True):
        if rec.get("require_extra"):
            ticker, _, side = key.partition("|")
            out.append(f"{ticker} {side}: {rec.get('n')} closes, avg {rec.get('avg_pnl_pct')}% — wait for a third family.")
    shown = {ln for ln in out}
    for key, rec in sorted(pairs, key=_rank, reverse=True)[:4]:
        ticker, _, side = key.partition("|")
        avg = rec.get("avg_pnl_pct")
        nn = rec.get("n")
        line = f"{ticker} {side}: {nn} closes, {rec.get('wins')}/{nn} green, avg {avg}%."
        if rec.get("skip") or rec.get("require_extra") or line in shown:
            continue
        out.append(line)
    for name, rec in sorted((state.get("strategies") or {}).items(), key=lambda kv: kv[1].get("n") or 0, reverse=True)[:3]:
        if rec.get("n"):
            out.append(
                f"Setup {name}: weight {rec.get('weight'):.2f} after {rec.get('n')} closes (avg {rec.get('avg_pnl_pct')}%)."
            )
    return out[:12]


def strategy_weight(state: dict, name: str) -> float:
    rec = (state.get("strategies") or {}).get(name) or {}
    try:
        return float(rec.get("weight") or 1.0)
    except (TypeError, ValueError):
        return 1.0


def apply_strategy_weights(candidates: list[Candidate], state: dict) -> list[Candidate]:
    if not candidates:
        return candidates
    out = []
    for cand in candidates:
        w = strategy_weight(state, cand.strategy)
        if abs(w - 1.0) < 1e-9:
            out.append(cand)
        else:
            out.append(replace(cand, conviction=round(cand.conviction * w, 3)))
    return out


def block_reason(
    ticker: str,
    direction: str,
    families: list[str],
    state: dict,
    strategies: list | None = None,
    window: str = "",
) -> str | None:
    """Why this fused idea should not print. None = take it.

    Order: session halt → window/side after an ugly FAIL → stack+window+side
    on any name → pair skip → exact recipe fingerprint → third-family gate.
    """
    ticker = ticker.upper()
    direction = direction.lower()
    sess = state.get("session") or {}
    if sess.get("halt"):
        return sess.get("why") or "session halt — no new TAKEs"
    wk = window_side_key(window, direction)
    ws = (state.get("window_sides") or {}).get(wk or "") or {}
    if wk and ws.get("skip"):
        return ws.get("why") or f"skip {wk}"
    pk = pattern_key(strategies, window, direction)
    pat = (state.get("patterns") or {}).get(pk or "") or {}
    if pk and pat.get("skip"):
        return pat.get("why") or f"skip stack {pk}"
    pair = (state.get("pairs") or {}).get(f"{ticker}|{direction}") or {}
    if pair.get("skip"):
        return pair.get("why") or f"skip {ticker} {direction}"
    key = fingerprint_key(ticker, direction, strategies, window)
    fp = (state.get("fingerprints") or {}).get(key or "") or {}
    if fp.get("skip"):
        return fp.get("why") or f"skip recipe {key}"
    if window:
        any_win = fingerprint_key(ticker, direction, strategies, "")
        generic = (state.get("fingerprints") or {}).get(any_win or "") or {}
        if generic.get("skip"):
            return generic.get("why") or f"skip recipe {any_win}"
    if pair.get("require_extra") and len(families or []) < 3:
        return pair.get("why") or "need a third family"
    return None


def summary(state: dict) -> dict:
    n = int(state.get("n") or 0)
    wins = int(state.get("wins") or 0)
    return {
        "ok": True,
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 3) if n else None,
        "lessons": list(state.get("lessons") or []),
        "skipped": list(state.get("skipped") or []),
        "blocked": list(state.get("blocked") or []),
        "autopsy": list(state.get("autopsy") or []),
        "session": state.get("session") or {},
        "policy": state.get("policy") or {},
        "accuracy": state.get("accuracy") or {},
        "cutoffs": state.get("cutoffs") or {"take": 65, "watch": 50},
        "note": state.get("note")
        or "Verdict is confidence. Prediction is whether that call was right given P&L.",
    }
