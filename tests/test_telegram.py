from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.clock import MarketClock
from pa.open_session.ledger import sync_book
from pa.open_session.scan import scan_open
from pa.open_session.telegram import notify_sell, notify_take, send_signal
from tests.conftest import make_settings
from tests.test_open_session import _pltr_sig, bullish_open, dump_after_spike

ET = ZoneInfo("America/New_York")


def test_send_signal_posts_buy_line(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    posted = {}

    class _Resp:
        def read(self):
            return b'{"ok": true, "result": {"message_id": 7}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=0):
        posted["url"] = req.full_url
        posted["body"] = req.data.decode()
        posted["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("pa.open_session.telegram.urllib.request.urlopen", fake_urlopen)
    line = "Buy DIA 530 Put Exp 9/4 for $1.71"
    assert send_signal(line, settings=settings) is True
    assert "/sendMessage" in posted["url"]
    assert "botTOKEN/" in posted["url"]
    assert line in posted["body"]
    assert posted["timeout"] == 5


def test_notify_take_skips_blank_and_skip_verdict(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda *a, **k: True)
    assert notify_take({"verdict": "SKIP", "text_buy": "Buy DIA 530 Put Exp 9/4 for $1.71"}, settings=settings) is False
    assert notify_take({"verdict": "TAKE", "text_buy": ""}, settings=settings) is False
    assert notify_take({"verdict": "TAKE", "text_buy": "Buy DIA 530 Put Exp 9/4 for $1.71"}, settings=settings) is True
    assert notify_take({"verdict": "TAKE", "text_buy": "Buy DIA 530 Put Exp 9/4 for $1.71"}) is False


def test_watch_is_sent_and_tagged(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    sent: list[str] = []
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda text, settings=None: sent.append(text) or True)
    row = {"verdict": "WATCH", "text_buy": "Buy DIA 530 Put Exp 9/4 for $1.71"}
    assert notify_take(row, settings=settings) is True
    assert sent == ["WATCH Buy DIA 530 Put Exp 9/4 for $1.71"]


def test_watch_is_muted_when_flag_off(tmp_path, monkeypatch) -> None:
    settings = make_settings(
        tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123", telegram_watch=False
    )
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda *a, **k: True)
    row = {"verdict": "WATCH", "text_buy": "Buy DIA 530 Put Exp 9/4 for $1.71"}
    assert notify_take(row, settings=settings) is False


def test_new_take_is_sent_once(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    sent: list[str] = []
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda text, settings=None: sent.append(text) or True)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    sig = _pltr_sig(verdict="TAKE", score=85, text_buy="Buy PLTR 185 Put Exp 8/31 for $1.45")
    sync_book(settings.data_dir, [sig], now, settings=settings)
    sync_book(settings.data_dir, [sig], now, settings=settings)
    assert sent == ["Buy PLTR 185 Put Exp 8/31 for $1.45"]


def test_entry_alert_is_not_resent_after_a_restart(monkeypatch, tmp_path) -> None:
    """A restart must not fire a second Buy for a signal already announced."""
    from pa.open_session import telegram as tg

    sent: list[str] = []
    monkeypatch.setattr(tg, "send_signal", lambda text, **kw: sent.append(text) or True)
    monkeypatch.setattr(tg, "enabled", lambda s: True)

    settings = make_settings(tmp_path)
    settings.study_mode = False
    row = {"verdict": "TAKE", "text_buy": "Buy SPY 660C @ 2.00"}
    assert tg.notify_take(row, settings=settings) is True
    assert row["telegram_sent"] is True
    # Same row, second pass (what a restart replaying the book looks like).
    assert tg.notify_take(row, settings=settings) is False
    assert sent == ["Buy SPY 660C @ 2.00"]


def test_failed_entry_alert_is_still_owed(monkeypatch, tmp_path) -> None:
    """A send Telegram refused leaves no flag, so the retry sweep can try again."""
    from pa.open_session import telegram as tg

    monkeypatch.setattr(tg, "send_signal", lambda text, **kw: False)
    monkeypatch.setattr(tg, "enabled", lambda s: True)
    settings = make_settings(tmp_path)
    row = {"verdict": "TAKE", "text_buy": "Buy SPY 660C @ 2.00"}
    assert tg.notify_take(row, settings=settings) is False
    assert "telegram_sent" not in row


def test_backfill_never_reannounces_old_sessions() -> None:
    """The new flag must not read a book of history as a backlog of unsent Buys."""
    from pa.open_session.ledger import _backfill_entry_alerts

    trades = [
        {"opened_at": "2026-09-16T10:00:00"},           # previous session
        {"opened_at": "2026-09-18T10:00:00"},           # today, still owed
        {"opened_at": "2026-09-17T10:00:00", "telegram_sent": False},  # explicit, keep
    ]
    _backfill_entry_alerts(trades, "2026-09-18")
    assert trades[0]["telegram_sent"] is True
    assert "telegram_sent" not in trades[1]
    assert trades[2]["telegram_sent"] is False


def test_study_mode_bands_every_alert(monkeypatch, tmp_path) -> None:
    """In study mode no line may look actionable, TAKE included."""
    from pa.open_session import telegram as tg

    sent: list[str] = []
    monkeypatch.setattr(tg, "send_signal", lambda text, **kw: sent.append(text) or True)
    monkeypatch.setattr(tg, "enabled", lambda s: True)

    settings = make_settings(tmp_path)
    settings.study_mode = True
    settings.telegram_watch = True
    for verdict in ("TAKE", "WATCH"):
        tg.notify_take({"verdict": verdict, "text_buy": "Buy SPY 660C @ 2.00"}, settings=settings)
    assert len(sent) == 2
    assert all(line.startswith(tg.STUDY_BANNER) for line in sent), sent
    # WATCH keeps its own tag underneath the banner.
    assert sent[1] == tg.STUDY_BANNER + "WATCH Buy SPY 660C @ 2.00"

    # With study mode off the lines are untouched.
    sent.clear()
    settings.study_mode = False
    tg.notify_take({"verdict": "TAKE", "text_buy": "Buy SPY 660C @ 2.00"}, settings=settings)
    assert sent == ["Buy SPY 660C @ 2.00"]


def test_scan_take_hits_telegram(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    sent: list[str] = []
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda text, settings=None: sent.append(text) or True)
    # Same reason as test_calibrate: the synthetic fixture contract is $0.66 and
    # the premium band refuses it, which would starve the alert this asserts on.
    monkeypatch.setattr("pa.open_session.contract.synthetic_premium", lambda *a, **k: 2.00)
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=ET)
    tape = scan_open(
        settings,
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": dump_after_spike("SPY")},
    )
    assert tape["strongest"] is not None
    assert tape["strongest"]["verdict"] == "TAKE"
    assert sent
    assert sent[0].startswith("Buy SPY")


def test_expiry_close_is_not_alerted(tmp_path, monkeypatch) -> None:
    # These fire days after the fact on a contract nobody can trade.
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda *a, **k: True)
    closed = {
        "status": "closed", "verdict": "TAKE", "ticker": "PLTR", "strike": 185,
        "direction": "put", "expiry": "2026-08-31", "exit": 1.45, "pnl_pct": 0.0,
    }
    assert notify_sell({**closed, "reason": "expired"}, settings=settings) is False
    assert notify_sell({**closed, "reason": "expired_unquoted"}, settings=settings) is False
    assert notify_sell({**closed, "reason": "take_profit"}, settings=settings) is True


def test_notify_sell_skips_open_and_no_settings(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda *a, **k: True)
    closed = {
        "status": "closed",
        "verdict": "TAKE",
        "ticker": "DIA",
        "strike": 530,
        "direction": "put",
        "expiry": "2026-09-04",
        "exit": 1.10,
        "pnl_pct": 55.0,
        "reason": "take_profit",
    }
    assert notify_sell({**closed, "verdict": "SKIP"}, settings=settings) is False
    assert notify_sell({**closed, "status": "open", "exit": None}, settings=settings) is False
    assert notify_sell(closed) is False
    assert notify_sell(closed, settings=settings) is True
    assert notify_sell(closed, settings=settings) is False


def test_close_sends_sell_once(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    sent: list[str] = []
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda text, settings=None: sent.append(text) or True)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    later = datetime(2026, 8, 31, 10, 13, tzinfo=ET)
    sig = _pltr_sig(verdict="TAKE", score=85, entry=2.90, target=4.50, stop=187.0, trigger=184.7)
    sync_book(settings.data_dir, [sig], now, settings=settings)
    assert sent[0].startswith("Buy PLTR")
    sync_book(
        settings.data_dir,
        [_pltr_sig(verdict="TAKE", score=85, entry=2.84, target=4.50, stop=187.0, trigger=184.7)],
        later,
        levels_by_ticker={"PLTR": {"last": 188.0, "vwap": 185.0, "ema9": 186.0, "ema9_prev": 185.5}},
        settings=settings,
    )
    sells = [x for x in sent if x.startswith("Sell ")]
    assert len(sells) == 1
    assert sells[0].startswith("Sell PLTR 185 Put Exp 8/31 for $2.84")
    assert sells[0] == "Sell PLTR 185 Put Exp 8/31 for $2.84 Stop -2% (-$6.00)"
    sync_book(settings.data_dir, [], later, settings=settings)
    assert [x for x in sent if x.startswith("Sell ")] == sells


def test_take_profit_sends_sell_line(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, telegram_bot_token="TOKEN", telegram_chat_id="123")
    sent: list[str] = []
    monkeypatch.setattr("pa.open_session.telegram.send_signal", lambda text, settings=None: sent.append(text) or True)
    now = datetime(2026, 8, 31, 10, 8, tzinfo=ET)
    later = datetime(2026, 8, 31, 10, 14, tzinfo=ET)
    sync_book(
        settings.data_dir,
        [_pltr_sig(verdict="TAKE", score=85, entry=1.00, target=1.75, stop=None, trigger=None)],
        now,
        settings=settings,
    )
    sync_book(
        settings.data_dir,
        [_pltr_sig(verdict="TAKE", score=85, entry=1.80, target=1.75, stop=None, trigger=None)],
        later,
        levels_by_ticker={"PLTR": {"last": 186.0, "vwap": 185.0, "ema9": 186.5, "ema9_prev": 185.5}},
        settings=settings,
    )
    sells = [x for x in sent if x.startswith("Sell ")]
    assert sells
    assert "Take Profit" in sells[0]
    assert "for $1.80" in sells[0]
