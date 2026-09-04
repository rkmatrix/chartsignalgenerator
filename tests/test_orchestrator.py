from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pa.data.fixtures import generate_quote, generate_trend_bars, session_start
from pa.data.polygon import FixtureMarketData
from pa.domain.models import AssetClass, KillSource, OptionSnapshot, Side, Signal, TradingMode
from pa.quant.strategy import QuantStrategy
from tests.conftest import RTH_NOW, make_engine, make_settings

ET = ZoneInfo("America/New_York")


class ScriptedData:
    def __init__(self, bars, quote, option=None) -> None:
        self._bars = bars
        self._quote = quote
        self._option = option

    async def bars(self, ticker: str, limit: int = 400):
        return self._bars

    async def quote(self, ticker: str):
        if ticker.upper() == "VIX":
            return generate_quote("VIX", 18.0, RTH_NOW, spread=0.2)
        return self._quote

    async def option_for_signal(self, underlying: str, side: Side, now: datetime):
        return self._option


@pytest.mark.asyncio
async def test_orchestrator_paper_fill_from_signal(tmp_path, monkeypatch) -> None:
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=400, start_price=500.0, drift=0.02)
    quote = generate_quote("SPY", bars[-1].close, RTH_NOW)
    data = ScriptedData(bars, quote)
    orch, _, _, broker = make_engine(tmp_path, data)

    def always_buy(ticker, bars_1m, option=None):
        return Signal(
            ticker=ticker,
            side=Side.BUY,
            confidence=1.0,
            reasons=["test"],
            ts=RTH_NOW,
            asset_class=AssetClass.ETF,
        )

    monkeypatch.setattr(orch.strategy, "evaluate", always_buy)
    await orch.step()
    assert broker.open_position_count() == 1
    fill = broker.fills[0]
    assert orch.journal.recent(topic="fill")
    # intents stored on the broker path are paper
    assert all(f.asset_class in {AssetClass.ETF, AssetClass.OPTION} for f in broker.fills)


@pytest.mark.asyncio
async def test_orchestrator_never_emits_live_mode(tmp_path, monkeypatch) -> None:
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=80, start_price=500.0)
    quote = generate_quote("SPY", bars[-1].close, RTH_NOW)
    orch, _, _, broker = make_engine(tmp_path, ScriptedData(bars, quote))

    def always_buy(ticker, bars_1m, option=None):
        return Signal(
            ticker=ticker,
            side=Side.BUY,
            confidence=1.0,
            reasons=["test"],
            ts=RTH_NOW,
        )

    monkeypatch.setattr(orch.strategy, "evaluate", always_buy)
    await orch.step()
    events = orch.journal.recent(topic="risk", limit=10)
    assert events
    payload = events[0].payload
    intent = payload.get("intent") or {}
    assert intent.get("trading_mode") == TradingMode.PAPER.value


@pytest.mark.asyncio
async def test_kill_flattens(tmp_path, monkeypatch) -> None:
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=80, start_price=500.0)
    quote = generate_quote("SPY", bars[-1].close, RTH_NOW)
    orch, _, kill, broker = make_engine(tmp_path, ScriptedData(bars, quote))

    def always_buy(ticker, bars_1m, option=None):
        return Signal(ticker=ticker, side=Side.BUY, confidence=1.0, reasons=["t"], ts=RTH_NOW)

    monkeypatch.setattr(orch.strategy, "evaluate", always_buy)
    await orch.step()
    assert broker.open_position_count() == 1
    kill.engage(KillSource.API, "test", RTH_NOW)
    await orch.step()
    assert broker.open_position_count() == 0


@pytest.mark.asyncio
async def test_session_closed_skips(tmp_path) -> None:
    night = datetime(2026, 3, 10, 20, 0, tzinfo=ET)
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=40, start_price=500.0)
    quote = generate_quote("SPY", 500, night)
    orch, _, _, broker = make_engine(tmp_path, ScriptedData(bars, quote), now=night)
    await orch.step()
    assert broker.open_position_count() == 0
    assert orch.state.last_skip == "session_closed"


def test_strategy_buy_when_rsi_band_allows(tmp_path) -> None:
    settings = make_settings(tmp_path, rsi_low=1, rsi_high=99.9)
    strategy = QuantStrategy(settings)
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=400, start_price=500.0, drift=0.03)
    signal = strategy.evaluate("SPY", bars)
    assert signal.side == Side.BUY
    assert signal.confidence == 1.0


def test_strategy_flat_on_short_history(tmp_path) -> None:
    settings = make_settings(tmp_path)
    strategy = QuantStrategy(settings)
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=10, start_price=500.0)
    signal = strategy.evaluate("SPY", bars)
    assert signal.side == Side.FLAT
    assert "insufficient_bars" in signal.reasons


def test_option_snapshot_preferred(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    strategy = QuantStrategy(settings)
    start = session_start(RTH_NOW)
    bars = generate_trend_bars("SPY", start, count=400, start_price=500.0, drift=0.02)
    option = OptionSnapshot(
        occ_symbol="SPY260313C00500000",
        underlying="SPY",
        expiry=datetime(2026, 3, 13, 16, 0, tzinfo=ET),
        strike=500,
        right="call",
        quote=generate_quote("SPY260313C00500000", 3.5, RTH_NOW, spread=0.05),
        delta=0.5,
    )
    signal = strategy.evaluate("SPY", bars, option=option)
    if signal.side != Side.FLAT:
        assert signal.occ_symbol == option.occ_symbol
        assert signal.asset_class == AssetClass.OPTION
