"""Webull paper orders stay on the sandbox and match the option the desk booked."""

from __future__ import annotations

import pytest

from pa.config import LiveTradingBlocked, Settings
from pa.execution.webull_paper import configured, mirror_close, mirror_open, option_order


def _settings(**overrides) -> Settings:
    base = dict(
        trading_mode="paper",
        paper_broker="webull",
        webull_app_key="key",
        webull_app_secret="secret",
        webull_host="api.sandbox.webull.com",
        _env_file=None,
    )
    base.update(overrides)
    return Settings(**base)


def _row() -> dict:
    return {
        "ticker": "SPY",
        "direction": "call",
        "strike": 773,
        "expiry": "2026-09-24",
        "entry": 0.5,
        "contracts": 1,
    }


def test_a_buy_is_a_single_leg_limit_at_the_entry() -> None:
    order = option_order(_row(), side="BUY", limit=0.5, client_order_id="a" * 32)
    assert order is not None
    assert order["order_type"] == "LIMIT"
    assert order["side"] == "BUY"
    assert order["position_intent"] == "BUY_TO_OPEN"
    assert order["limit_price"] == "0.50"
    assert order["instrument_type"] == "OPTION"
    assert len(order["client_order_id"]) <= 32
    leg = order["legs"][0]
    assert leg["option_type"] == "CALL"
    assert leg["strike_price"] == "773.00"
    assert leg["option_expire_date"] == "2026-09-24"


def test_a_sell_closes_the_long() -> None:
    order = option_order(_row(), side="SELL", limit=0.42, client_order_id="b" * 32)
    assert order["side"] == "SELL"
    assert order["position_intent"] == "SELL_TO_CLOSE"
    assert order["legs"][0]["side"] == "SELL"


def test_nothing_is_sent_without_keys() -> None:
    settings = _settings(webull_app_key="", webull_app_secret="")
    assert configured(settings) is False
    row = _row()
    mirror_open(row, settings)
    assert "webull_buy_id" not in row


def test_the_live_host_is_rejected() -> None:
    with pytest.raises(LiveTradingBlocked):
        _settings(webull_host="api.webull.com")


def test_an_open_is_sent_once_and_a_close_only_after_it_fills(monkeypatch) -> None:
    sent: list[dict] = []

    class Fake:
        def __init__(self, settings) -> None:
            pass

        def place(self, order: dict) -> tuple[bool, str]:
            sent.append(order)
            return True, "ok"

    monkeypatch.setattr("pa.execution.webull_paper.WebullPaper", Fake)
    settings = _settings()
    row = _row()
    mirror_open(row, settings)
    mirror_open(row, settings)
    assert len(sent) == 1
    assert row["webull_buy_sent"] is True
    assert sent[0]["side"] == "BUY"

    row["exit"] = 0.42
    mirror_close(row, settings)
    assert len(sent) == 2
    assert sent[1]["side"] == "SELL"
    mirror_close(row, settings)
    assert len(sent) == 2


def test_a_close_does_not_sell_a_contract_this_desk_never_bought() -> None:
    row = {**_row(), "exit": 0.4}
    mirror_close(row, _settings())
    assert "webull_sell_id" not in row


def test_options_go_to_the_individual_account_not_events_cash() -> None:
    from pa.execution.webull_paper import _account_id_from

    listing = [
        {"account_id": "EVT", "account_class": "EVENTS_CASH"},
        {"account_id": "MRG", "account_class": "INDIVIDUAL_MARGIN"},
        {"account_id": "FUT", "account_class": "FUTURES"},
        {"account_id": "CSH", "account_class": "INDIVIDUAL_CASH"},
    ]
    assert _account_id_from(listing) == "MRG"
    assert _account_id_from(listing[2:]) == "CSH"
    assert _account_id_from([listing[0], listing[2]]) == ""
