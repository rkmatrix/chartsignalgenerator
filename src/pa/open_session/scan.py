from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.babysitter.advise import PLAN_STOP_0DTE_PCT
from pa.chart_trader.universe import HIGH_VOLUME
from pa.clock import MarketClock
from pa.config import Settings
from pa.open_session.bars import load_intraday_1m
from pa.open_session.clock import minutes_since_open, minutes_until_close, session_phase
from pa.open_session.fuse import FusedSignal, fuse
from pa.open_session.grade import score_for, verdict_for
from pa.open_session.levels import compute_levels
from pa.open_session.playbook import apply_playbook, session_window
from pa.open_session.setups import SOLO_STRATEGIES, setups_for

ORB_MINUTES = 15
CACHE_SECONDS = 20
WAVE_LAST_ENTRY_MINUTES = 30
OFF_TAPE = {"weekend", "holiday", "closed"}
INDEX = {"SPY", "SPX"}
ET = ZoneInfo("America/New_York")


def macd_block(direction: str, macd_hist: float | None) -> str | None:
    """Refuse a signal the MACD histogram disagrees with.

    The desk reads trend and volatility but carried no oscillator. Over 3,161
    TAKE-grade signals across 17 tickers and 25 sessions, the 1,204 the
    histogram disagreed with averaged -0.0137% over a 15 bar hold while the
    1,957 it agreed with averaged +0.0073%, and that held to four decimals in
    both halves of the sample split by date. RSI was weaker and both Stochastic
    and an overbought/oversold RSI rule helped in one half and hurt in the
    other, so only this one is wired in.

    Silence rather than a veto when the histogram is unavailable: early in a
    session there are not yet 35 bars to build the signal line from, and a
    missing reading is not a disagreement.
    """
    if macd_hist is None:
        return None
    side = str(direction or "").lower()
    if side == "call" and macd_hist <= 0:
        return f"MACD histogram {macd_hist:+.4f} is not building upward"
    if side == "put" and macd_hist >= 0:
        return f"MACD histogram {macd_hist:+.4f} is not building downward"
    return None


def tape_block(direction: str, mom15: float | None) -> str | None:
    """Refuse a signal that bets against the way the tape is already moving.

    The most stable divider in the dataset. Of 4,162 signals across 17 tickers
    and 25 sessions, the 906 pointing against the prior 15 minutes averaged
    -0.0141% over the next 15 while the rest averaged +0.0020%, and the losing
    side keeps its sign in both halves of a date split (-0.0146% / -0.0137%).
    Almost nothing else tested does: dropping the worst decile, fading the most
    extended decile, and orb_fade all flip sign between halves.

    This removes a documented loss. It does not create an edge, and the comment
    is here so nobody later mistakes it for one: what remains after this veto
    still averages +0.0013%, against a round trip costing 0.0796% of the
    underlying. The desk is not profitable because of this rule, it is merely
    less unprofitable.

    Silence when the reading is missing — under 16 bars there is no prior 15
    minutes to disagree with, and absence is not disagreement.
    """
    if mom15 is None:
        return None
    side = str(direction or "").lower()
    if side == "call" and mom15 < 0:
        return f"tape is {mom15:+.3f}% over the prior 15m, against a call"
    if side == "put" and mom15 > 0:
        return f"tape is {mom15:+.3f}% over the prior 15m, against a put"
    return None


def _spy_bias(bars) -> str | None:
    if len(bars) < 5:
        return None
    prev, last = bars[-5].close, bars[-1].close
    if prev <= 0:
        return None
    move = (last - prev) / prev * 100
    if move >= 0.15:
        return "bullish"
    if move <= -0.15:
        return "bearish"
    return None


def _fresh_json(path: Path, max_age: float) -> dict | None:
    if not path.exists():
        return None
    age = datetime.now().timestamp() - path.stat().st_mtime
    if age > max_age:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, payload: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    return payload


def _empty_tape(now: datetime, phase: str) -> dict:
    return {
        "ok": True,
        "ts": now.isoformat(),
        "phase": phase,
        "note": _phase_note(phase),
        "strongest": None,
        "signals": [],
        "queue": [],
        "rows": [],
        "scanned": [],
        "book": [],
        "learn": {"ok": True, "n": 0, "wins": 0, "lessons": [], "skipped": [], "blocked": [], "autopsy": []},
    }


def scan_open(
    settings: Settings,
    clock: MarketClock,
    tickers: list[str] | None = None,
    bars_by_ticker: dict | None = None,
    fetch: bool = True,
) -> dict:
    now = clock.now()
    session_day = now.astimezone(ET).date() if now.tzinfo else now.date()
    phase = session_phase(now, clock, orb_minutes=ORB_MINUTES)
    path = settings.data_dir / "history" / "strongest.json"

    if bars_by_ticker is None and not fetch:
        payload = _empty_tape(now, phase)
        from pa.open_session.ledger import repair_book, table_rows

        payload["book"] = table_rows(repair_book(settings.data_dir))
        from pa.open_session.learn import rebuild, summary

        payload["learn"] = summary(rebuild(settings.data_dir, today=session_day))
        return _write(path, payload)

    if bars_by_ticker is None and fetch:
        cached = _fresh_json(path, CACHE_SECONDS)
        if cached and cached.get("phase") == phase:
            sigs = cached.get("signals") or []
            if not sigs or sigs[0].get("text_buy"):
                from pa.open_session.ledger import table_rows, watch_exits
                from pa.open_session.learn import rebuild, summary

                levels = {row["ticker"]: row for row in cached.get("rows") or [] if row.get("ticker")}
                book = watch_exits(
                    settings.data_dir,
                    now,
                    levels_by_ticker=levels,
                    minutes_to_close=minutes_until_close(now, clock),
                    quote_exit=True,
                    settings=settings,
                )
                if path.exists():
                    from pa.open_session.calibrate import allows_take

                    learn_now = rebuild(settings.data_dir, today=session_day)
                    kept = []
                    for s in cached.get("signals") or []:
                        win = str(s.get("window") or session_window(minutes_since_open(now)) or "")
                        ok, why = allows_take(
                            window=win,
                            direction=str(s.get("direction") or ""),
                            strategies=list(s.get("strategies") or []),
                            state=learn_now,
                        )
                        if str(s.get("verdict") or "").upper() == "TAKE" and not ok:
                            s["verdict"] = "WATCH"
                            s["calibrate"] = why
                            continue
                        kept.append(s)
                    cached["signals"] = kept
                    cached["strongest"] = None if not kept else kept[0]
                    cached["book"] = table_rows(book)
                    cached["learn"] = summary(learn_now)
                    return cached

    names = [t.upper() for t in (tickers or HIGH_VOLUME)]
    lead = [t for t in ("SPY", "SPX") if t in names]
    names = lead + [t for t in names if t not in lead]

    ideas: list[FusedSignal] = []
    rows: list[dict] = []
    spy_bars = None
    priors: dict[str, float | None] = {}
    from pa.open_session.learn import apply_strategy_weights, block_reason, rebuild, summary
    from pa.open_session.calibrate import allows_take
    from pa.open_session.ledger import load_book, open_take_tickers, other_side_block, sides_taken_today

    learn_state = rebuild(settings.data_dir, today=session_day)
    # Fold in anything that closed since the last scan, then read the weights
    # once for this pass so every card is judged by the same model.
    from pa.open_session.bandit import features as bandit_features
    from pa.open_session.bandit import learn_from_book, load_model

    learn_from_book(settings.data_dir)
    bandit_model = load_model(settings.data_dir)
    bandit_on = bool(getattr(settings, "bandit_gate", True))
    elapsed = minutes_since_open(now)
    day = session_day.isoformat()
    taken = sides_taken_today(load_book(settings.data_dir).get("trades") or [], day)
    use_wave = bool(getattr(settings, "use_alphawave", False))
    wave_exits: list[dict] = []

    for ticker in names:
        prior_close = None
        wave_bars: list = []
        if bars_by_ticker is not None:
            packed = bars_by_ticker.get(ticker) or []
            if isinstance(packed, tuple):
                bars, prior_close = packed[0], packed[1] if len(packed) > 1 else None
            else:
                bars = packed
            source = "injected"
            wave_bars = list(bars or [])
        elif use_wave:
            from pa.open_session.bars import load_recent_1m

            recent, source, prior_close = load_recent_1m(ticker, settings.data_dir)
            wave_bars = recent
            bars = [b for b in recent if b.ts.astimezone(ET).date() == session_day]
        else:
            bars, source, prior_close = load_intraday_1m(ticker, settings.data_dir)
            wave_bars = list(bars or [])
        priors[ticker] = prior_close
        if ticker in INDEX and not spy_bars:
            spy_bars = bars
        lv = compute_levels(ticker, bars, orb_minutes=ORB_MINUTES, prior_close=prior_close)
        if use_wave:
            from pa.open_session.alphawave import evaluate, to_fused

            wave = evaluate(wave_bars, now)
            event = wave.event
            fused = to_fused(ticker, event, elapsed) if event is not None else None
            if event is not None and event.kind in {"TP_CALL", "TP_PUT"}:
                wave_exits.append(
                    {
                        "ticker": ticker,
                        "direction": "call" if event.kind == "TP_CALL" else "put",
                    }
                )
            cands = []
            _, pb = apply_playbook([], ticker, elapsed)
            if fused:
                fused.structure_score = 82
        else:
            cands = setups_for(ticker, bars, orb_minutes=ORB_MINUTES, prior_close=prior_close) if bars else []
            cands, pb = apply_playbook(cands, ticker, elapsed)
            cands = apply_strategy_weights(cands, learn_state)
            fused = fuse(cands, spy_bias=_spy_bias(spy_bars or []) if ticker not in INDEX else None)
        if fused and not use_wave:
            fused.playbook = pb.why
            fused.window = pb.window
        learn_block = None
        if fused and not use_wave:
            n_fam = len(fused.families or [])
            solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
            fused.structure_score = score_for(fused.conviction, families=n_fam, solo=solo)
            if not fused.vetoed:
                learn_block = block_reason(
                    fused.ticker,
                    fused.direction,
                    fused.families,
                    learn_state,
                    strategies=fused.strategies,
                    window=pb.window,
                )
                if not learn_block:
                    learn_block = tape_block(fused.direction, lv.mom15)
                    if learn_block:
                        fused.veto_reason = "tape"
                if not learn_block:
                    learn_block = macd_block(fused.direction, lv.macd_hist)
                    if learn_block:
                        fused.veto_reason = "macd"
                if not learn_block:
                    learn_block = other_side_block(fused.ticker, fused.direction, taken)
                    if learn_block:
                        fused.veto_reason = "other_side"
                if learn_block:
                    fused.vetoed = True
                    if not fused.veto_reason:
                        fused.veto_reason = "learn"
                    fused.thesis = learn_block
        if fused:
            ideas.append(fused)
        rows.append(
            {
                "ticker": ticker,
                "source": source,
                "last": lv.last,
                "vwap": lv.vwap,
                "ema9": lv.ema9,
                "ema21": lv.ema21,
                "ema9_prev": lv.ema9_prev,
                "pm_high": lv.premarket_high,
                "pm_low": lv.premarket_low,
                "orb_high": lv.orb_high,
                "orb_low": lv.orb_low,
                "gap_pct": lv.gap_pct,
                "inside_pm": lv.inside_premarket,
                "rth_n": lv.rth_n,
                "candidates": [c.strategy for c in cands],
                "alphawave": wave.position if use_wave else None,
                "playbook": pb.as_dict(),
                "fused": None
                if fused is None
                else {
                    "direction": fused.direction,
                    "conviction": fused.conviction,
                    "strategies": fused.strategies,
                    "vetoed": fused.vetoed,
                    "thesis": fused.thesis,
                    "learn_block": learn_block,
                    "playbook": fused.playbook,
                    "window": fused.window,
                },
            }
        )

    live = [
        i
        for i in sorted(ideas, key=lambda x: (x.conviction, len(x.families)), reverse=True)
        if not i.vetoed and i.direction in {"call", "put"}
    ]
    # SPX used to win this tie and silently drop the matching SPY idea. It is the
    # wrong survivor: an SPX contract runs ~$50, so risk_block refuses it at the
    # $225 cap and the SPY trade has already been thrown away — the pair went
    # SPX -$99, SPY +$266 while the desk kept choosing SPX. Deduping SPY against
    # SPX is crowding_block's job anyway, and it does it at position level where
    # the cheaper contract has already been chosen.
    if use_wave:
        # The indicator fires on a closed bar whenever the market is open,
        # including the first hour and the lunch window the old playbook skipped.
        if phase in {"weekend", "holiday", "closed", "premarket"}:
            live = []
        # A 0DTE opened now is flattened at 20 minutes to the bell, so it
        # pays the whole spread for a few minutes of exposure.
        left = minutes_until_close(now, clock)
        if left is not None and left < WAVE_LAST_ENTRY_MINUTES:
            live = []
    elif phase != "hunt":
        live = []
    attach_contracts = fetch and bars_by_ticker is None
    cards = []
    for n, i in enumerate(live, start=1):
        card = _signal_card(i, rank=n, now=now, data_dir=settings.data_dir, fetch_chain=attach_contracts)
        card["score"] = int(getattr(i, "structure_score", None) or score_for(i.conviction, families=len(i.families or [])))
        card["verdict"] = verdict_for(card["score"], learn_state)
        if getattr(i, "signal_bar", ""):
            card["signal_bar"] = i.signal_bar
        win = str(getattr(i, "window", "") or card.get("window") or session_window(elapsed) or "")
        card["window"] = win
        wave_card = "alphawave" in (i.families or [])
        if wave_card:
            # The chart indicator is the signal. The learner and the first-hour
            # ban were fit on the old setup stack, and applying them here would
            # throw away the print the chart just confirmed.
            card["verdict"] = "TAKE"
            card["calibrate"] = "AlphaWave closed-bar signal"
        ok, why = allows_take(
            window=win,
            direction=str(i.direction or ""),
            strategies=list(i.strategies or []),
            state=learn_state,
        )
        if card["verdict"] == "TAKE" and not ok and not wave_card:
            card["verdict"] = "WATCH"
            card["calibrate"] = why

        # The learner gets the last word on capital, and only on capital.
        #
        # It is trained on realised option P&L, so its estimate is already net of
        # the spread. Committing only where the pessimistic bound clears zero
        # means a context has to be profitable even after its own uncertainty is
        # subtracted -- one good week cannot open the account.
        #
        # A refusal demotes to WATCH rather than dropping the signal, which
        # matters more than it looks: the print still goes out, still gets
        # graded, and still teaches the model. That is how the agent keeps
        # learning about contexts it has declined instead of freezing into
        # whatever it happened to try first.
        mean_pct, sd_pct = bandit_model.predict(bandit_features(card))
        card["bandit_mean"] = round(mean_pct, 2)
        card["bandit_lcb"] = round(mean_pct - sd_pct, 2)
        card["bandit_n"] = bandit_model.n
        if bandit_on and card["verdict"] == "TAKE" and card["bandit_lcb"] <= 0 and not wave_card:
            card["verdict"] = "WATCH"
            card["calibrate"] = (
                f"learner: {mean_pct:+.1f}% +/- {sd_pct:.1f} — lower bound not clear of the spread"
            )
        cards.append(card)
    live_takes = open_take_tickers(load_book(settings.data_dir).get("trades") or [], day)
    if not use_wave:
        _one_live_take(cards, live_takes)
    signals = [c for c in cards if c.get("verdict") == "TAKE"]
    watch = [c for c in cards if c.get("verdict") == "WATCH"]
    to_book = [c for c in cards if c.get("verdict") == "TAKE" or (c.get("verdict") == "WATCH" and not c.get("queued"))]
    rest = signals[1:]
    levels = {row["ticker"]: row for row in rows if row.get("ticker")}
    from pa.open_session.ledger import sync_book, table_rows

    book = sync_book(
        settings.data_dir,
        to_book,
        now,
        levels_by_ticker=levels,
        minutes_to_close=minutes_until_close(now, clock),
        quote_exit=attach_contracts,
        settings=settings,
        wave_exits=wave_exits,
    )
    payload = {
        "ok": True,
        "ts": now.isoformat(),
        "phase": phase,
        "scanned": names,
        "note": _phase_note(phase, bool(signals), len(signals), len(names), n_watch=len(watch)),
        "strongest": None if not signals else signals[0],
        "signals": signals,
        "queue": [
            {
                "ticker": s["ticker"],
                "direction": s["direction"],
                "conviction": s["conviction"],
                "stop": s.get("stop"),
                "trigger": s.get("trigger"),
                "verdict": s.get("verdict"),
            }
            for s in rest + watch
        ],
        "rows": rows,
        "book": table_rows(book),
        "learn": summary(rebuild(settings.data_dir, today=session_day)),
    }
    return _write(path, payload)


def _one_live_take(cards: list[dict], open_tickers: set[str]) -> None:
    """Keep at most one new TAKE. The live name may still update."""
    live = {str(t).upper() for t in open_tickers}
    reserved = bool(live)
    for card in cards:
        if str(card.get("verdict") or "").upper() != "TAKE":
            continue
        ticker = str(card.get("ticker") or "").upper()
        if ticker in live:
            continue
        if reserved:
            card["verdict"] = "WATCH"
            card["queued"] = "one_take"
        else:
            reserved = True
            live.add(ticker)


def _signal_card(idea, rank: int, now: datetime | None = None, data_dir: Path | None = None, fetch_chain: bool = False) -> dict:
    plan = PLAN_STOP_0DTE_PCT
    stop_txt = f"{idea.ticker} through {idea.stop:.2f}" if idea.stop else "VWAP + 9EMA flip"
    trigger_txt = f" or back through {idea.trigger:.2f}" if idea.trigger else ""
    card = {
        "rank": rank,
        "ticker": idea.ticker,
        "direction": idea.direction,
        "conviction": idea.conviction,
        "strategies": idea.strategies,
        "families": idea.families,
        "thesis": idea.thesis,
        "playbook": getattr(idea, "playbook", "") or "",
        "window": getattr(idea, "window", "") or "",
        "last": idea.last,
        "trigger": idea.trigger,
        "stop": idea.stop,
        "plan_stop_pct": plan,
        "score": score_for(idea.conviction),
        "verdict": "TAKE",
        "invalidation": f"Sell if {stop_txt}{trigger_txt} or option −{plan:.0f}%",
        "text": (
            f"{idea.direction.upper()} {idea.ticker} @ {idea.last:.2f} "
            f"— {idea.thesis}"
        ),
        "text_sl": (
            f"SL {idea.stop:.2f} underlying · option −{plan:.0f}%"
            if idea.stop
            else f"SL option −{plan:.0f}% · VWAP/EMA flip"
        ),
    }
    if data_dir is not None and now is not None and idea.last:
        from pa.open_session.contract import contract_for

        try:
            c = contract_for(
                idea.ticker,
                idea.direction,
                idea.last,
                now,
                data_dir,
                fetch=fetch_chain,
                conviction=idea.conviction,
            )
            card.update(
                {
                    "strike": c["strike"],
                    "opt": c["opt"],
                    "expiry": c["expiry"],
                    "entry": c["entry"],
                    "target": c["target"],
                    "take_profit_pct": c["take_profit_pct"],
                    "premium_source": c["premium_source"],
                    "entry_bid": c.get("entry_bid"),
                    "entry_ask": c.get("entry_ask"),
                    "text_buy": c["text_buy"],
                    "text_sell": c["text_sell"],
                    "text": c["text_buy"],
                }
            )
        except Exception:
            pass
    return card


def _phase_note(phase: str, has_signal: bool = False, n_signals: int = 0, n_scanned: int = 0, n_watch: int = 0) -> str:
    if phase == "hunt" and not has_signal:
        extra = f" {n_watch} WATCH print{'s' if n_watch != 1 else ''} booked." if n_watch else ""
        return (
            f"Scanned {n_scanned or 'all'} names. No TAKE yet "
            f"(first hour is WATCH until that slice is 95%, or need confluence later).{extra}"
        )
    if phase == "hunt" and has_signal:
        cap = " One live TAKE at a time so the next print can learn from the last close."
        return (
            f"Scanned the full list. {n_signals} strong Call/Put"
            f"{'s' if n_signals != 1 else ''} passed confluence.{cap}"
        )
    return {
        "weekend": "Market closed (weekend). Strongest Call/Put fires after Monday 15m ORB + 2 confirming minutes (~09:47 ET).",
        "holiday": "Holiday — no new entries.",
        "closed": "Session closed. Watching premarket when it starts (04:00 ET).",
        "premarket": "Premarket: building high/low. No Call/Put until cash open and ORB complete.",
        "orb_building": "Opening range is still printing. No trade until 15m ORB + 2 closes beyond it.",
        "confirming": "ORB just completed. Waiting for two closes beyond the range.",
        "lunch": "Lunch blackout — no new entries. Existing positions still get hold/exit advice.",
        "late": "Too close to the close for a new 0DTE.",
        "hunt": "Open playbook is live. Every name is scanned; only two-family confluence becomes a signal.",
    }.get(phase, phase)
