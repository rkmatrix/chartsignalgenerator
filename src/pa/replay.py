from __future__ import annotations

from datetime import datetime

from pa.clock import MarketClock
from pa.config import Settings
from pa.domain.models import AssetClass, Bar, Fill, OrderIntent, Quote, Side, TradingMode
from pa.execution.paper import PaperBroker
from pa.quant.strategy import QuantStrategy
from pa.risk.gates import KillSwitch, RiskAgent


def replay_bars(
    ticker: str,
    bars: list[Bar],
    settings: Settings,
    *,
    rsi_high: float | None = None,
) -> dict:
    """Walk 1m bars through quant + risk + paper. No network."""
    if rsi_high is not None:
        settings = settings.model_copy(update={"rsi_high": rsi_high})
    clock = MarketClock(now_fn=lambda: bars[-1].ts if bars else datetime.now())
    kill = KillSwitch(settings.kill_file)
    broker = PaperBroker(settings.starting_equity, settings.slippage_bps)
    risk = RiskAgent(settings, kill, clock)
    strategy = QuantStrategy(settings)
    fills: list[Fill] = []
    signals = 0
    for i in range(40, len(bars)):
        now = bars[i].ts
        clock._now_fn = lambda ts=now: ts
        if clock.skip_reason(now):
            continue
        window = bars[: i + 1]
        last = window[-1].close
        q = Quote(ticker=ticker, ts=now, bid=last - 0.02, ask=last + 0.02, last=last)
        if broker.has_position(ticker):
            broker.manage_open(
                {ticker: q},
                now,
                settings.trail_pct,
                settings.scale_out_gain_pct,
                settings.scale_out_fraction,
            )
            continue
        signal = strategy.evaluate(ticker, window)
        if signal.side == Side.FLAT:
            continue
        signals += 1
        intent = OrderIntent(
            ticker=ticker,
            qty=max(1, risk.size_qty(broker.equity(), last, AssetClass.ETF)),
            side=signal.side,
            asset_class=AssetClass.ETF,
            trading_mode=TradingMode.PAPER,
            ts=now,
            stop_price=last * 0.98 if signal.side == Side.BUY else last * 1.02,
        )
        decision = risk.evaluate(
            intent,
            now=now,
            equity=broker.equity(),
            day_pnl_pct=broker.day_pnl_pct(),
            open_positions=broker.open_position_count(),
            has_position=False,
            quote=q,
            vix_last=18.0,
        )
        if decision.action.value != "allow" or not decision.intent:
            continue
        fills.append(broker.submit(decision.intent, q, now))
    if broker.open_position_count() and bars:
        last_bar = bars[-1]
        q = Quote(
            ticker=ticker,
            ts=last_bar.ts,
            bid=last_bar.close - 0.02,
            ask=last_bar.close + 0.02,
            last=last_bar.close,
        )
        broker.flatten_all({ticker: q}, last_bar.ts)
    return {
        "ticker": ticker,
        "bars": len(bars),
        "signals": signals,
        "fills": len(fills),
        "pnl": broker.day_pnl(),
        "equity": broker.equity(),
    }


def walk_forward(ticker: str, bars: list[Bar], settings: Settings) -> dict:
    """Grid-search EMA on the first 70% of bars; score the last 30%. Do not auto-apply."""
    split = max(80, int(len(bars) * 0.7))
    train, test = bars[:split], bars[split:]
    grid = []
    for fast in (5, 9, 13):
        for slow in (21, 34):
            if fast >= slow:
                continue
            trial = settings.model_copy(update={"ema_fast": fast, "ema_slow": slow, "rsi_high": 99.0})
            result = replay_bars(ticker, train, trial)
            grid.append({"ema_fast": fast, "ema_slow": slow, "pnl": result["pnl"], "fills": result["fills"]})
    grid.sort(key=lambda r: r["pnl"], reverse=True)
    best = grid[0] if grid else None
    holdout = None
    if best and len(test) > 50:
        holdout_settings = settings.model_copy(
            update={"ema_fast": best["ema_fast"], "ema_slow": best["ema_slow"], "rsi_high": 99.0}
        )
        holdout = replay_bars(ticker, test, holdout_settings)
    return {"grid": grid, "best": best, "holdout": holdout, "applied": False}


def replay_cli() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from pa.config import Settings
    from pa.data.fixtures import generate_trend_bars, session_start

    et = ZoneInfo("America/New_York")
    settings = Settings(_env_file=None, trading_mode="paper", rsi_high=99.9)
    settings.ensure_data_dir()
    bars = generate_trend_bars("SPY", session_start(datetime(2026, 3, 10, 10, 30, tzinfo=et)))
    print(replay_bars("SPY", bars, settings, rsi_high=99.9))


if __name__ == "__main__":
    replay_cli()

