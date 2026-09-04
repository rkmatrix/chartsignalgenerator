from __future__ import annotations

import json
from pathlib import Path

from pa.chart_trader.history import load_year_bars
from pa.chart_trader.hunter import hunt
from pa.chart_trader.params import ChartParams
from pa.chart_trader.pine import to_pine, tradingview_url
from pa.config import Settings


def run_chart_trader(
    ticker: str = "SPY",
    year: int = 2025,
    settings: Settings | None = None,
) -> dict:
    settings = settings or Settings(_env_file=None, trading_mode="paper")
    settings.ensure_data_dir()
    bars, source = load_year_bars(
        ticker,
        year,
        settings.data_dir,
        interval="1h",
        polygon_key=settings.polygon_api_key,
    )
    result = hunt(ticker, bars, year=year)
    result["data_source"] = source
    result["tradingview_url"] = tradingview_url(ticker)
    if result.get("best"):
        params = ChartParams(**result["best"]["params"])
        pine = to_pine(params, ticker)
        pine_path = settings.data_dir / "history" / f"PA_ChartTrader_{ticker}_{year}.pine"
        pine_path.parent.mkdir(parents=True, exist_ok=True)
        pine_path.write_text(pine, encoding="utf-8")
        result["pine_path"] = str(pine_path)
        result["pine"] = pine
    out_path = settings.data_dir / "history" / f"hunt_{ticker}_{year}.json"
    slim = {k: v for k, v in result.items() if k != "pine"}
    out_path.write_text(json.dumps(slim, default=str, indent=2), encoding="utf-8")
    result["result_path"] = str(out_path)
    return result


def main() -> None:
    result = run_chart_trader()
    best = result.get("best") or {}
    oos = result.get("oos") or {}
    print(
        json.dumps(
            {
                "source": result.get("data_source"),
                "bars": result.get("bars"),
                "generations": result.get("generations"),
                "hit_target": result.get("hit_target"),
                "train": {
                    "precision": best.get("train_precision"),
                    "n": best.get("train_n"),
                },
                "oos": oos,
                "params": best.get("params"),
                "tv": result.get("tradingview_url"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
