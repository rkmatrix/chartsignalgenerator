from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pa.clock import MarketClock
from pa.open_session.grade import verdict_for
from pa.open_session.learn import (
    WEIGHT_FLOOR,
    apply_strategy_weights,
    block_reason,
    rebuild,
)
from pa.open_session.fuse import fuse
from pa.open_session.ledger import save_book
from pa.open_session.playbook import apply_playbook
from pa.open_session.scan import scan_open
from pa.open_session.setups import Candidate, setups_for
from tests.conftest import make_settings
from tests.test_open_session import bullish_open

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 8, 31)


def _closed(
    ticker: str,
    direction: str,
    pnl_pct: float,
    day: str,
    strategies: list[str] | None = None,
) -> dict:
    entry = 1.0
    exit_px = round(entry * (1.0 + pnl_pct / 100.0), 4)
    return {
        "id": f"{ticker}-{direction}-{day}",
        "status": "closed",
        "opened_at": f"{day}T10:00:00-04:00",
        "closed_at": f"{day}T11:00:00-04:00",
        "ticker": ticker,
        "direction": direction,
        "opt": "P" if direction == "put" else "C",
        "entry": entry,
        "exit": exit_px,
        "pnl_dollars": round((exit_px - entry) * 100.0, 2),
        "pnl_pct": pnl_pct,
        "verdict": "TAKE",
        "strategies": strategies or ["orb", "ema_align"],
        "window": "first_hour",
    }


def test_two_losing_prints_skip_that_side(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("QQQ", "put", -12.0, "2026-08-28"),
                _closed("QQQ", "put", -9.0, "2026-08-29"),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    pair = state["pairs"]["QQQ|put"]
    assert pair["skip"] is True
    assert block_reason("QQQ", "put", ["level", "trend"], state)
    assert block_reason("QQQ", "call", ["level", "trend"], state) is None


def test_one_loss_does_not_skip(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(settings.data_dir, {"trades": [_closed("QQQ", "put", -8.0, "2026-08-31")]})
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["pairs"]["QQQ|put"]["skip"] is False
    assert block_reason("QQQ", "put", ["level", "trend"], state) is None
    same = block_reason(
        "QQQ",
        "put",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    )
    assert same
    assert "not repeating" in same.lower() or "fail" in same.lower()
    assert block_reason(
        "QQQ",
        "put",
        ["mean_rev", "trend"],
        state,
        strategies=["bb_rejection", "ema_align"],
        window="first_hour",
    ) is None
    assert block_reason(
        "QQQ",
        "call",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    ) is None


def test_one_fail_yesterday_may_retry(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(settings.data_dir, {"trades": [_closed("QQQ", "put", -8.0, "2026-08-28")]})
    state = rebuild(settings.data_dir, today=TODAY)
    assert block_reason(
        "QQQ",
        "put",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    ) is None


def test_two_recipe_fails_sit_out_without_killing_the_name(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("QQQ", "put", -3.0, "2026-08-28"),
                _closed("QQQ", "put", -3.0, "2026-08-29"),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["pairs"]["QQQ|put"]["skip"] is False
    blocked = [k for k, rec in (state.get("fingerprints") or {}).items() if rec.get("skip")]
    assert any("QQQ|put|ema_align+orb" in k for k in blocked)
    assert block_reason(
        "QQQ",
        "put",
        ["mean_rev", "trend", "gap"],
        state,
        strategies=["bb_rejection", "ema_align", "gap_and_go"],
        window="first_hour",
    ) is None
    assert any("Do not TAKE QQQ put" in x for x in state["lessons"])


def test_skip_fail_does_not_block_recipe(tmp_path) -> None:
    settings = make_settings(tmp_path)
    missed = _closed("QQQ", "put", 40.0, "2026-08-31")
    missed["verdict"] = "SKIP"
    save_book(settings.data_dir, {"trades": [missed]})
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["accuracy"]["skip"]["n"] == 1
    assert not state["blocked"]
    assert block_reason(
        "QQQ",
        "put",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    ) is None


def test_flat_take_does_not_block_recipe(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(settings.data_dir, {"trades": [_closed("QQQ", "put", 0.0, "2026-08-31")]})
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["accuracy"]["take"]["passes"] == 1
    assert not state["blocked"]
    assert block_reason(
        "QQQ",
        "put",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    ) is None


def test_ugly_loss_needs_third_family(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("SPY", "call", -15.0, "2026-08-27"),
                _closed("SPY", "call", 4.0, "2026-08-28"),
                _closed("SPY", "call", -12.0, "2026-08-31"),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    rec = state["pairs"]["SPY|call"]
    assert rec["skip"] is False
    assert rec["require_extra"] is True
    assert block_reason("SPY", "call", ["level", "trend"], state)
    assert block_reason("SPY", "call", ["level", "trend", "gap"], state) is None


def test_one_ugly_loss_does_not_need_third_family(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(settings.data_dir, {"trades": [_closed("SPY", "put", -20.2, "2026-08-31")]})
    state = rebuild(settings.data_dir, today=TODAY)
    rec = state["pairs"]["SPY|put"]
    assert rec["skip"] is False
    assert rec["require_extra"] is False
    assert block_reason("SPY", "put", ["level", "trend"], state) is None


def test_winners_do_not_boost_conviction(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("NVDA", "call", 80.0, "2026-08-28", ["orb"]),
                _closed("MSFT", "call", 40.0, "2026-08-29", ["orb"]),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["strategies"]["orb"]["weight"] == 1.0
    cands = [
        Candidate("SPY", "call", "orb", "level", 1.2, 100.0, 99.0, "orb", 100.0),
        Candidate("SPY", "call", "ema_align", "trend", 1.0, 100.0, 99.0, "ema", 100.0),
    ]
    out = apply_strategy_weights(cands, state)
    assert out[0].conviction == 1.2


def test_losing_setup_is_downweighted_not_boosted(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("JPM", "call", -12.0, "2026-08-28", ["orb"]),
                _closed("DIA", "put", -8.0, "2026-08-29", ["orb"]),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["strategies"]["orb"]["weight"] == WEIGHT_FLOOR
    cand = Candidate("SPY", "call", "orb", "level", 1.2, 100.0, 99.0, "orb", 100.0)
    out = apply_strategy_weights([cand], state)
    assert out[0].conviction == round(1.2 * WEIGHT_FLOOR, 3)
    assert out[0].conviction < 1.2


def test_scan_does_not_reprint_failed_recipe(tmp_path) -> None:
    settings = make_settings(tmp_path)
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    bars = bullish_open("QQQ")
    cands, pb = apply_playbook(setups_for("QQQ", bars), "QQQ", 35)
    fused = fuse(cands)
    assert fused is not None
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("QQQ", fused.direction, -12.0, "2026-08-31", list(fused.strategies)),
            ]
        },
    )
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY", "QQQ"],
        bars_by_ticker={"SPY": bullish_open("SPY"), "QQQ": bars},
    )
    names = {s["ticker"] for s in tape["signals"]}
    assert "SPY" not in names
    assert "QQQ" not in names
    qqq_row = next(r for r in tape["rows"] if r["ticker"] == "QQQ")
    spy_row = next(r for r in tape["rows"] if r["ticker"] == "SPY")
    assert qqq_row["fused"]["vetoed"] is True
    assert spy_row["fused"]["vetoed"] is True
    assert qqq_row["fused"]["learn_block"]
    assert spy_row["fused"]["learn_block"]
    assert tape["learn"]["blocked"]
    assert tape["learn"]["autopsy"]


def test_scan_skips_repeat_loser(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("QQQ", "call", -12.0, "2026-08-28"),
                _closed("QQQ", "call", -9.0, "2026-08-29"),
            ]
        },
    )
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY", "QQQ"],
        bars_by_ticker={"SPY": bullish_open("SPY"), "QQQ": bullish_open("QQQ")},
    )
    names = {s["ticker"] for s in tape["signals"]}
    assert "QQQ" not in names
    qqq_row = next(r for r in tape["rows"] if r["ticker"] == "QQQ")
    assert qqq_row["fused"]["vetoed"] is True
    assert qqq_row["fused"]["learn_block"]


def test_verdict_and_prediction_match_signalvalidator() -> None:
    from pa.open_session.grade import prediction_for, score_for, tune_take_cut, verdict_for

    assert verdict_for(80) == "TAKE"
    assert verdict_for(60) == "WATCH"
    assert verdict_for(40) == "SKIP"
    assert score_for(2.2) == 85
    assert score_for(1.1, families=1, solo=True) == 71
    assert score_for(3.3, families=3) == 92
    assert prediction_for("TAKE", 23.9) == "PASS"
    assert prediction_for("TAKE", -10.9) == "FAIL"
    assert prediction_for("WATCH", -3.0) == "FAIL"
    assert prediction_for("SKIP", -20.0) == "PASS"
    assert prediction_for("SKIP", 40.0) == "FAIL"
    assert prediction_for("EXECUTE", 5.0) == "PASS"
    assert tune_take_cut(4, 4) == 65
    assert tune_take_cut(8, 14) == 80


def test_take_bar_rises_after_fails(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("AAA", "call", -12.0, "2026-08-25"),
                _closed("BBB", "call", -11.0, "2026-08-26"),
                _closed("CCC", "put", -9.0, "2026-08-27"),
                _closed("DDD", "put", -8.0, "2026-08-28"),
                _closed("EEE", "call", -15.0, "2026-08-29"),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    assert state["cutoffs"]["take"] == 80
    assert state["accuracy"]["take"]["passes"] == 0
    assert state["accuracy"]["take"]["n"] == 5
    assert verdict_for(66, state) == "WATCH"
    assert verdict_for(85, state) == "TAKE"
    assert verdict_for(90, state) == "TAKE"


def test_ugly_fail_blocks_same_stack_other_names_not_the_other_side(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(settings.data_dir, {"trades": [_closed("META", "call", -27.7, "2026-08-31")]})
    state = rebuild(settings.data_dir, today=TODAY)
    assert (state.get("session") or {}).get("halt") is False
    same = block_reason(
        "GOOGL",
        "call",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    )
    assert same
    other_stack = block_reason(
        "BAC",
        "call",
        ["mean_rev", "trend"],
        state,
        strategies=["level_retest", "ema_align"],
        window="first_hour",
    )
    assert other_stack
    put = block_reason(
        "QQQ",
        "put",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    )
    assert put is None


def test_two_tiny_fails_block_that_stack_not_the_session(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("QQQ", "put", -3.0, "2026-08-31"),
                _closed("IWM", "put", -3.0, "2026-08-31"),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    assert (state.get("session") or {}).get("halt") is False
    assert block_reason(
        "SPY",
        "put",
        ["level", "trend"],
        state,
        strategies=["orb", "ema_align"],
        window="first_hour",
    )
    assert block_reason(
        "SPY",
        "put",
        ["mean_rev", "trend", "gap"],
        state,
        strategies=["bb_rejection", "ema_align", "gap_and_go"],
        window="first_hour",
    ) is None


def test_session_halts_after_sub_40_percent_morning(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                _closed("AAA", "call", -12.0, "2026-08-31"),
                _closed("BBB", "call", -11.0, "2026-08-31"),
                _closed("CCC", "put", 4.0, "2026-08-31"),
            ]
        },
    )
    state = rebuild(settings.data_dir, today=TODAY)
    sess = state.get("session") or {}
    assert sess.get("halt") is True
    assert sess.get("n") == 3
    assert block_reason("NVDA", "put", ["level", "trend"], state)


def test_one_open_take_queues_the_rest(tmp_path) -> None:
    settings = make_settings(tmp_path)
    save_book(
        settings.data_dir,
        {
            "trades": [
                {
                    "id": "META-call-2026-08-31",
                    "status": "open",
                    "opened_at": "2026-08-31T09:50:00-04:00",
                    "ticker": "META",
                    "direction": "call",
                    "opt": "C",
                    "entry": 1.0,
                    "mark": 1.0,
                    "verdict": "TAKE",
                    "score": 90,
                    "prediction": "pending",
                    "strategies": ["orb", "ema_align"],
                    "window": "first_hour",
                }
            ]
        },
    )
    hunt = datetime(2026, 8, 31, 10, 5, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY", "QQQ"],
        bars_by_ticker={"SPY": bullish_open("SPY"), "QQQ": bullish_open("QQQ")},
    )
    assert tape["signals"] == []
    assert tape["strongest"] is None
    queued = {s["ticker"] for s in tape["queue"]}
    assert "SPY" in queued
    assert "QQQ" in queued
    book_takes = [t for t in tape["book"] if str(t.get("verdict") or "").upper() == "TAKE" and t.get("status") == "open"]
    assert {t["ticker"] for t in book_takes} == {"META"}
