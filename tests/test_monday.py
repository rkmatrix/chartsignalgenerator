from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pa.chart_trader.hunter import compact_grid, hunt
from pa.chart_trader.monday import next_monday_open, option_card, pick_from_chain
from pa.chart_trader.scan import run_universe
from pa.chart_trader.universe import HIGH_VOLUME
from pa.data.fixtures import generate_trend_bars
from pa.domain.models import Bar, Side, Timeframe
from tests.conftest import make_settings

ET = ZoneInfo("America/New_York")


def test_next_monday_from_saturday() -> None:
    now = datetime(2026, 8, 29, 22, 30, tzinfo=ET)
    assert next_monday_open(now) == date(2026, 8, 31)


def test_option_card_copy() -> None:
    card = option_card(
        "TSLA",
        Side.BUY,
        358.0,
        date(2026, 8, 31),
        date(2026, 8, 29),
        oos_precision=0.18,
        last_confidence=0.6,
        oos_n=11,
        wilson_lo=0.05,
        chain_calls=[{"strike": 360.0, "bid": 1.4, "ask": 1.5, "last": 1.45}],
        chain_expiry=date(2026, 8, 31),
    )
    assert card["text_buy"] == "Buy TSLA 360 Call Exp 8/31 for $1.50"
    assert card["text_sell"].startswith("Sell TSLA 360 Call Exp 8/31 for $")
    assert "Take profit" in card["text_tp"]
    assert card["target"] > card["entry"]


def test_pick_chain_nearest_strike() -> None:
    rows = [
        {"strike": 350.0, "bid": 1.0, "ask": 1.1, "last": 1.05},
        {"strike": 360.0, "bid": 1.4, "ask": 1.5, "last": 1.45},
        {"strike": 370.0, "bid": 0.8, "ask": 0.9, "last": 0.85},
    ]
    picked = pick_from_chain(rows, 361.0, True)
    assert picked is not None
    assert picked["strike"] == 360.0
    assert picked["entry"] == 1.5


def test_compact_grid_size() -> None:
    assert len(compact_grid()) == 24


def test_compact_hunt_synthetic() -> None:
    bars = generate_trend_bars("QQQ", datetime(2025, 1, 6, 10, 0, tzinfo=ET), count=400, start_price=400.0, drift=0.04)
    stamped = []
    start = datetime(2025, 1, 6, 10, 0, tzinfo=ET)
    for i, bar in enumerate(bars):
        stamped.append(bar.model_copy(update={"ts": start + timedelta(hours=i)}))
    result = hunt("QQQ", stamped, year=2025, target=0.6, min_trades=1, compact=True)
    assert result["generations"] == 24


def test_universe_scan_offline(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    start = datetime(2025, 1, 6, 10, 0, tzinfo=ET)
    raw = generate_trend_bars("SPY", start, count=200, start_price=500.0, drift=0.05)

    def fake_load(ticker, year, data_dir, interval="1h", polygon_key=""):
        bars = [
            Bar(
                ticker=ticker,
                ts=start + timedelta(hours=i),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                timeframe=Timeframe.M15,
            )
            for i, bar in enumerate(raw)
        ]
        return bars, "fixture"

    monkeypatch.setattr("pa.chart_trader.scan.load_year_bars", fake_load)
    now = datetime(2026, 8, 29, 22, 0, tzinfo=ET)
    out = run_universe(settings, tickers=["SPY", "TSLA"], year=2025, now=now, fetch_options=False)
    assert {r["ticker"] for r in out["rows"]} == {"SPY", "TSLA"}
    assert out["monday"] == "2026-08-31"
    assert (settings.data_dir / "history" / "monday_signals.json").exists()


def test_high_volume_has_core_names() -> None:
    for name in ("SPY", "SPX", "QQQ", "TSLA", "NVDA", "AAPL"):
        assert name in HIGH_VOLUME
