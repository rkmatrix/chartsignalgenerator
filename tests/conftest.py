from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.bus.events import EventBus
from pa.clock import MarketClock
from pa.config import Settings
from pa.execution.paper import PaperBroker
from pa.orchestrator.engine import Orchestrator, RuntimeState
from pa.quant.strategy import QuantStrategy
from pa.risk.gates import KillSwitch, RiskAgent
from pa.storage.journal import EventJournal

ET = ZoneInfo("America/New_York")

# Tuesday during regular hours, after the open buffer, before lunch.
RTH_NOW = datetime(2026, 3, 10, 10, 30, tzinfo=ET)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        trading_mode="paper",
        polygon_api_key="",
        watchlist="SPY",
        starting_equity=100_000.0,
        daily_drawdown_pct=2.0,
        max_open_positions=3,
        max_risk_per_trade_pct=1.0,
        data_dir=data,
        kill_file=data / "KILL",
        journal_db=data / "pa.db",
        earnings_blackout="",
        signalvalidator_url="",
        # Off by default here so the engine tests keep testing the engine. The
        # learner starts with no evidence, so its lower bound cannot clear the
        # spread and it would demote every TAKE to WATCH -- correct in
        # production, but it would quietly gut assertions about playbooks,
        # calibration and alert routing that have nothing to do with it.
        # The gate has its own coverage, on and off, in test_bandit.py.
        bandit_gate=False,
    )
    kwargs.update(overrides)
    settings = Settings(_env_file=None, **kwargs)
    settings.ensure_data_dir()
    return settings


def make_clock(now: datetime = RTH_NOW) -> MarketClock:
    return MarketClock(now_fn=lambda: now)


def make_engine(tmp_path: Path, data, now: datetime = RTH_NOW, **setting_overrides):
    settings = make_settings(tmp_path, **setting_overrides)
    clock = make_clock(now)
    kill = KillSwitch(settings.kill_file)
    broker = PaperBroker(settings.starting_equity, settings.slippage_bps)
    risk = RiskAgent(settings, kill, clock)
    strategy = QuantStrategy(settings)
    journal = EventJournal(settings.journal_db)
    bus = EventBus()
    state = RuntimeState(started_at=now, using_fixtures=True)
    orch = Orchestrator(
        settings=settings,
        clock=clock,
        data=data,
        strategy=strategy,
        risk=risk,
        kill=kill,
        broker=broker,
        journal=journal,
        bus=bus,
        state=state,
    )
    return orch, settings, kill, broker
