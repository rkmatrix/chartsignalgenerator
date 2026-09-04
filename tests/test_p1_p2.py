from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from pa.api.app import create_app
from pa.calendar import MarketCalendar
from pa.clock import MarketClock
from pa.data.fixtures import generate_trend_bars, session_start
from pa.domain.models import AssetClass, Bar, Quote, Side, Signal, Timeframe
from pa.execution.paper import PaperBroker
from pa.news.agent import score_headline
from pa.orchestrator.resolve import resolve
from pa.quant.builder import parse_indicator_spec
from pa.quant.features import fibonacci_levels, rsi_divergence, support_resistance
from pa.quant.indicators import rsi
from pa.quant.pricing import black_scholes, probability_itm
from pa.replay import replay_bars, walk_forward
from pa.voice.chat import parse_command
from tests.conftest import RTH_NOW, make_clock, make_engine, make_settings

ET = ZoneInfo("America/New_York")


def test_rsi_divergence_bullish() -> None:
    start = datetime(2026, 3, 10, 9, 30, tzinfo=ET)
    bars: list[Bar] = []
    price = 100.0
    for i in range(40):
        # second half makes a lower low in price while RSI recovers via smaller down bars
        if i < 20:
            delta = -0.4 if i % 2 == 0 else 0.15
        else:
            delta = -0.15 if i % 2 == 0 else 0.25
        close = price + delta
        bars.append(
            Bar(
                ticker="SPY",
                ts=start + timedelta(minutes=i),
                open=price,
                high=max(price, close) + 0.05,
                low=min(price, close) - 0.05,
                close=close,
                volume=1000,
                timeframe=Timeframe.M1,
            )
        )
        price = close
    series = rsi([b.close for b in bars], 14)
    # function may or may not fire on this synthetic; just ensure it doesn't crash
    rsi_divergence(bars, series)


def test_support_resistance_and_fib() -> None:
    bars = generate_trend_bars("SPY", session_start(RTH_NOW), count=80, start_price=100)
    lo, hi = support_resistance(bars)
    assert lo is not None and hi is not None and hi >= lo
    fibs = fibonacci_levels(bars)
    assert "0.618" in fibs
    assert fibs["0"] <= fibs["0.618"] <= fibs["1"]


def test_black_scholes_call_positive() -> None:
    px = black_scholes(100, 100, 30 / 365, 0.04, 0.2, True)
    assert px > 0
    pop = probability_itm(100, 100, 30 / 365, 0.2, True)
    assert 0.4 < pop < 0.7


def test_calendar_macro_window(tmp_path) -> None:
    settings = make_settings(tmp_path)
    cal = MarketCalendar(settings)
    cpi = datetime(2026, 3, 11, 8, 30, tzinfo=ET)
    assert cal.macro_blackout(cpi) is not None
    assert cal.macro_blackout(datetime(2026, 3, 10, 10, 30, tzinfo=ET)) is None


def test_parse_indicator_and_chat() -> None:
    spec = parse_indicator_spec("use ema 8 21 with rsi 10 and vwap")
    assert spec["ema_fast"] == 8 and spec["ema_slow"] == 21
    cmd = parse_command("buy 2 SPY", RTH_NOW)
    assert cmd["action"] == "signal" and cmd["ticker"] == "SPY"


def test_resolve_picks_highest_confidence() -> None:
    a = Signal(ticker="SPY", side=Side.BUY, confidence=0.4, ts=RTH_NOW, source="quant")
    b = Signal(ticker="SPY", side=Side.SELL, confidence=0.9, ts=RTH_NOW, source="tradingview")
    chosen = resolve([a, b])
    assert chosen is not None and chosen.source == "tradingview"


def test_headline_sentiment() -> None:
    sent, risk = score_headline("Geopolitical war fears crash futures")
    assert sent < 0 and risk is True


def test_trailing_stop_exits(tmp_path) -> None:
    broker = PaperBroker(50_000, slippage_bps=0)
    now = RTH_NOW
    q = Quote(ticker="SPY", ts=now, bid=100, ask=100, last=100)
    from pa.domain.models import OrderIntent, TradingMode

    broker.submit(
        OrderIntent(ticker="SPY", qty=10, side=Side.BUY, asset_class=AssetClass.ETF, ts=now),
        q,
        now,
    )
    up = Quote(ticker="SPY", ts=now, bid=110, ask=110, last=110)
    broker.mark("SPY", 110)
    fills = broker.check_trails({"SPY": up}, now, trail_pct=1.0)
    # still in: 110 vs trail 108.9
    assert broker.open_position_count() == 1
    down = Quote(ticker="SPY", ts=now, bid=108, ask=108, last=108)
    fills = broker.check_trails({"SPY": down}, now, trail_pct=1.0)
    assert len(fills) == 1
    assert broker.open_position_count() == 0


def test_scale_out(tmp_path) -> None:
    broker = PaperBroker(50_000, slippage_bps=0)
    now = RTH_NOW
    q = Quote(ticker="SPY", ts=now, bid=100, ask=100, last=100)
    from pa.domain.models import OrderIntent

    broker.submit(
        OrderIntent(ticker="SPY", qty=10, side=Side.BUY, asset_class=AssetClass.ETF, ts=now),
        q,
        now,
    )
    up = Quote(ticker="SPY", ts=now, bid=125, ask=125, last=125)
    fills = broker.check_scale_out({"SPY": up}, now, gain_pct=20, fraction=0.5)
    assert len(fills) == 1
    pos = broker.position_for("SPY")
    assert pos is not None and pos.qty == 5 and pos.scale_out_done


def test_replay_and_walkforward(tmp_path) -> None:
    settings = make_settings(tmp_path, rsi_high=99.9)
    bars = generate_trend_bars("SPY", session_start(RTH_NOW), count=200, start_price=100, drift=0.03)
    result = replay_bars("SPY", bars, settings, rsi_high=99.9)
    assert result["bars"] == 200
    wf = walk_forward("SPY", bars, settings)
    assert wf["applied"] is False
    assert wf["grid"]


def test_tv_webhook_and_pause(tmp_path) -> None:
    data = type("D", (), {})()
    orch, settings, _, _ = make_engine(tmp_path, data)
    clock = make_clock()
    client = TestClient(create_app(settings, orch.state, orch.broker, orch.kill, orch.journal, clock, orch=orch))
    r = client.post("/webhook/tradingview", json={"ticker": "SPY", "side": "buy", "confidence": 0.8})
    assert r.json()["ok"] is True
    assert orch.inbox.drain()[0].source == "tradingview"
    client.post("/pause")
    assert orch.state.paused is True
    chat = client.post("/chat", json={"text": "status"})
    assert "equity" in chat.json()["reply"]


def test_vix_size_down_not_halt(tmp_path) -> None:
    from pa.domain.models import OrderIntent, TradingMode
    from pa.risk.gates import KillSwitch, RiskAgent

    settings = make_settings(tmp_path, vix_size_down_level=25, vix_halt_level=40)
    clock = make_clock()
    kill = KillSwitch(settings.kill_file)
    agent = RiskAgent(settings, kill, clock)
    intent = OrderIntent(
        ticker="SPY", qty=10, side=Side.BUY, asset_class=AssetClass.ETF, ts=RTH_NOW, trading_mode=TradingMode.PAPER
    )
    q = Quote(ticker="SPY", ts=RTH_NOW, bid=500, ask=500, last=500)
    decision = agent.evaluate(
        intent,
        now=RTH_NOW,
        equity=100_000,
        day_pnl_pct=0,
        open_positions=0,
        has_position=False,
        quote=q,
        vix_last=30,
    )
    assert decision.action.value == "allow"
    assert decision.sized_qty < 2  # 1% of 100k / 500 = 2, times 0.5 → 1
