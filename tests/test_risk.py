from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pydantic import ValidationError

from pa.clock import MarketClock
from pa.config import LiveTradingBlocked, Settings
from pa.domain.models import AssetClass, KillSource, OrderIntent, Quote, RiskAction, Side, TradingMode
from pa.execution.paper import PaperBroker
from pa.risk.gates import KillSwitch, RiskAgent
from tests.conftest import make_settings

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 3, 10, 10, 30, tzinfo=ET)


def _quote(last: float = 500.0) -> Quote:
    return Quote(ticker="SPY", ts=NOW, bid=last - 0.01, ask=last + 0.01, last=last)


def _intent(qty: int = 10) -> OrderIntent:
    return OrderIntent(
        ticker="SPY",
        qty=qty,
        side=Side.BUY,
        asset_class=AssetClass.ETF,
        trading_mode=TradingMode.PAPER,
        ts=NOW,
    )


def _agent(tmp_path, **overrides) -> tuple[RiskAgent, KillSwitch, PaperBroker]:
    settings = make_settings(tmp_path, **overrides)
    clock = MarketClock(now_fn=lambda: NOW)
    kill = KillSwitch(settings.kill_file)
    broker = PaperBroker(settings.starting_equity)
    return RiskAgent(settings, kill, clock), kill, broker


def test_live_order_intent_rejected() -> None:
    with pytest.raises(ValidationError):
        OrderIntent(
            ticker="SPY",
            qty=1,
            side=Side.BUY,
            asset_class=AssetClass.ETF,
            trading_mode="live",  # type: ignore[arg-type]
            ts=NOW,
        )


def test_kill_switch_halts(tmp_path) -> None:
    agent, kill, broker = _agent(tmp_path)
    kill.engage(KillSource.API, "test", NOW)
    decision = agent.evaluate(
        _intent(),
        now=NOW,
        equity=broker.equity(),
        day_pnl_pct=0.0,
        open_positions=0,
        has_position=False,
        quote=_quote(),
        vix_last=None,
    )
    assert decision.action == RiskAction.HALT
    assert "kill_switch" in decision.reasons


def test_drawdown_halts(tmp_path) -> None:
    agent, kill, broker = _agent(tmp_path, daily_drawdown_pct=2.0)
    decision = agent.evaluate(
        _intent(),
        now=NOW,
        equity=broker.equity(),
        day_pnl_pct=-2.5,
        open_positions=0,
        has_position=False,
        quote=_quote(),
        vix_last=None,
    )
    assert decision.action == RiskAction.HALT
    assert kill.is_killed()


def test_oversize_is_reduced(tmp_path) -> None:
    agent, _, broker = _agent(tmp_path, max_risk_per_trade_pct=1.0, starting_equity=100_000)
    decision = agent.evaluate(
        _intent(qty=10_000),
        now=NOW,
        equity=100_000,
        day_pnl_pct=0.0,
        open_positions=0,
        has_position=False,
        quote=_quote(500),
        vix_last=None,
    )
    assert decision.action == RiskAction.ALLOW
    # 1% of 100k = 1000 notional / 500 = 2 shares
    assert decision.sized_qty == 2


def test_max_positions_denies(tmp_path) -> None:
    agent, _, _ = _agent(tmp_path, max_open_positions=1)
    decision = agent.evaluate(
        _intent(),
        now=NOW,
        equity=100_000,
        day_pnl_pct=0.0,
        open_positions=1,
        has_position=False,
        quote=_quote(),
        vix_last=None,
    )
    assert decision.action == RiskAction.DENY
    assert "max_open_positions" in decision.reasons


def test_earnings_blackout(tmp_path) -> None:
    agent, _, _ = _agent(tmp_path, earnings_blackout="SPY")
    decision = agent.evaluate(
        _intent(),
        now=NOW,
        equity=100_000,
        day_pnl_pct=0.0,
        open_positions=0,
        has_position=False,
        quote=_quote(),
        vix_last=None,
    )
    assert decision.action == RiskAction.DENY
    assert "earnings_blackout" in decision.reasons


def test_settings_blocks_live(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(LiveTradingBlocked):
        Settings()
