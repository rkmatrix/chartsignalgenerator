from __future__ import annotations

import json

from pa.open_session.eod import build_summary, format_summary, send_eod
from tests.conftest import make_settings


def _write_book(data_dir, trades) -> None:
    path = data_dir / "history"
    path.mkdir(parents=True, exist_ok=True)
    (path / "signal_book.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")


def _row(ticker, *, opened="2026-09-09", closed="2026-09-09", verdict="TAKE", pred="PASS", pnl=10.0, status="closed"):
    return {
        "id": f"{ticker}-call-{opened}",
        "ticker": ticker,
        "verdict": verdict,
        "prediction": pred,
        "status": status,
        "opened_at": f"{opened}T10:35:00-04:00",
        "closed_at": None if status != "closed" else f"{closed}T11:05:00-04:00",
        "pnl_dollars": None if status != "closed" else pnl,
        "exit": None if status != "closed" else 1.5,
    }


def test_summary_counts_grades_and_extremes(tmp_path) -> None:
    settings = make_settings(tmp_path)
    _write_book(
        settings.data_dir,
        [
            _row("AAPL", pnl=274.0),
            _row("V", pred="FAIL", pnl=-27.0),
            _row("QQQ", verdict="WATCH", pnl=50.0),
            _row("MSFT", status="open", pred="pending"),
            # Yesterday: counts toward overall P/L only.
            _row("NFLX", opened="2026-09-08", closed="2026-09-08", pnl=-100.0),
        ],
    )
    data = build_summary(settings.data_dir, "2026-09-09")
    assert data["generated"] == 4
    assert data["verdicts"] == {"TAKE": 3, "WATCH": 1}
    assert data["graded"] == 3
    assert data["passes"] == 2
    assert data["accuracy_pct"] == 66.7
    assert data["today_pl"] == 297.0
    assert data["overall_pl"] == 197.0
    assert data["open_now"] == 1
    assert data["best"] == {"ticker": "AAPL", "pnl": 274.0}
    assert data["worst"] == {"ticker": "V", "pnl": -27.0}

    text = format_summary(data)
    assert "PA Desk EOD Wed Sep 9" in text
    assert "Signals generated    4" in text
    assert "Prediction accuracy  2/3  (66.7%)" in text
    assert "Today's P/L          +$297" in text
    assert "Overall P/L          +$197" in text
    assert "Highest profit       AAPL  +$274" in text
    assert "Heavy loss           V  -$27" in text
    assert text.isascii()


def test_empty_session_is_silent_unless_forced(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    _write_book(settings.data_dir, [])
    sent: list[str] = []
    monkeypatch.setattr(
        "pa.open_session.telegram.send_signal",
        lambda text, settings=None, monospace=False: sent.append(text) or True,
    )
    ok, _ = send_eod(settings.data_dir, day="2026-09-09", settings=settings)
    assert ok is False and sent == []

    ok, _ = send_eod(settings.data_dir, day="2026-09-09", settings=settings, force=True)
    assert ok is True and len(sent) == 1


def test_no_extremes_when_all_flat(tmp_path) -> None:
    settings = make_settings(tmp_path)
    _write_book(settings.data_dir, [_row("SPY", pnl=0.0)])
    text = format_summary(build_summary(settings.data_dir, "2026-09-09"))
    assert "Highest profit" not in text
    assert "Heavy loss" not in text
