from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pa.domain.models import AssetClass, OrderIntent, Quote, Side
from pa.execution.paper import DuplicateOrderError, PaperBroker

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 3, 10, 10, 30, tzinfo=ET)


def _quote(last: float = 100.0) -> Quote:
    return Quote(ticker="SPY", ts=NOW, bid=last - 0.02, ask=last + 0.02, last=last)


def test_buy_fill_and_position() -> None:
    broker = PaperBroker(starting_equity=50_000, slippage_bps=5)
    intent = OrderIntent(
        ticker="SPY",
        qty=10,
        side=Side.BUY,
        asset_class=AssetClass.ETF,
        ts=NOW,
    )
    fill = broker.submit(intent, _quote(100), NOW)
    assert fill.price > fill.expected_mid
    assert broker.has_position("SPY")
    pos = broker.position_for("SPY")
    assert pos is not None and pos.qty == 10
    assert broker.cash < 50_000


def test_flatten_returns_to_flat() -> None:
    broker = PaperBroker(starting_equity=50_000, slippage_bps=0)
    quote = _quote(100)
    broker.submit(
        OrderIntent(ticker="SPY", qty=10, side=Side.BUY, asset_class=AssetClass.ETF, ts=NOW),
        quote,
        NOW,
    )
    fills = broker.flatten_all({"SPY": quote}, NOW)
    assert len(fills) == 1
    assert broker.open_position_count() == 0


def test_duplicate_client_order_id() -> None:
    broker = PaperBroker(10_000)
    intent = OrderIntent(
        ticker="SPY",
        qty=1,
        side=Side.BUY,
        asset_class=AssetClass.ETF,
        ts=NOW,
        client_order_id="abc123",
    )
    broker.submit(intent, _quote(), NOW)
    with pytest.raises(DuplicateOrderError):
        broker.submit(intent, _quote(), NOW)


def test_stop_flatten() -> None:
    broker = PaperBroker(50_000, slippage_bps=0)
    broker.submit(
        OrderIntent(
            ticker="SPY",
            qty=10,
            side=Side.BUY,
            asset_class=AssetClass.ETF,
            ts=NOW,
            stop_price=99.0,
        ),
        _quote(100),
        NOW,
    )
    down = _quote(98.5)
    fills = broker.check_stops({"SPY": down}, NOW)
    assert len(fills) == 1
    assert broker.open_position_count() == 0


def test_option_multiplier_cash() -> None:
    broker = PaperBroker(50_000, slippage_bps=0)
    quote = Quote(ticker="SPY260313C00500000", ts=NOW, bid=2.0, ask=2.0, last=2.0)
    broker.submit(
        OrderIntent(
            ticker="SPY",
            qty=2,
            side=Side.BUY,
            asset_class=AssetClass.OPTION,
            occ_symbol="SPY260313C00500000",
            ts=NOW,
        ),
        quote,
        NOW,
    )
    assert broker.cash == 50_000 - (2 * 2.0 * 100)
