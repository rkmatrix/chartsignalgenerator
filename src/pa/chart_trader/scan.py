from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.chart_trader.history import load_year_bars
from pa.chart_trader.hunter import hunt
from pa.chart_trader.monday import (
    confidence_label,
    fetch_chain_rows,
    next_monday_open,
    option_card,
    wilson_interval,
)
from pa.chart_trader.params import ChartParams
from pa.chart_trader.reader import read_chart
from pa.chart_trader.universe import HIGH_VOLUME
from pa.config import Settings
from pa.domain.models import Side

ET = ZoneInfo("America/New_York")


def _concat(a, b):
    if not b:
        return a
    cutoff = a[-1].ts if a else None
    extra = [bar for bar in b if cutoff is None or bar.ts > cutoff]
    return list(a) + extra


def scan_ticker(
    ticker: str,
    settings: Settings,
    year: int = 2025,
    now: datetime | None = None,
    fetch_options: bool = True,
) -> dict:
    now = now or datetime.now(tz=ET)
    bars, source = load_year_bars(
        ticker,
        year,
        settings.data_dir,
        interval="1h",
        polygon_key=settings.polygon_api_key,
    )
    result = hunt(ticker, bars, year=year, compact=True, min_trades=12)
    live_source = source
    live = bars
    try:
        more, live_source = load_year_bars(
            ticker,
            now.year,
            settings.data_dir,
            interval="1h",
            polygon_key=settings.polygon_api_key,
        )
        if now.year != year:
            live = _concat(bars, more)
        else:
            live = more or bars
    except Exception:
        live_source = source
    params = ChartParams(**result["best"]["params"]) if result.get("best") else ChartParams()
    last = read_chart(ticker, live, params)
    trade_side = last.side
    if trade_side == Side.FLAT:
        bull = sum(1 for reason in last.reasons if reason.endswith(":bull"))
        bear = sum(1 for reason in last.reasons if reason.endswith(":bear"))
        if bull > bear:
            trade_side = Side.BUY
        elif bear > bull:
            trade_side = Side.SELL
    oos = result.get("oos") or {}
    oos_n = int(oos.get("n") or 0)
    oos_wins = int(oos.get("wins") or 0)
    oos_p = float(oos.get("precision") or 0.0)
    lo, _, _ = wilson_interval(oos_wins, oos_n)
    best = result.get("best") or {}
    spot = live[-1].close if live else 0.0
    row = {
        "ticker": ticker,
        "bars": result.get("bars"),
        "data_source": source,
        "live_source": live_source,
        "live_ts": live[-1].ts.isoformat() if live else None,
        "spot": spot,
        "train_precision": best.get("train_precision"),
        "train_n": best.get("train_n"),
        "oos_precision": oos_p,
        "oos_n": oos_n,
        "oos_wins": oos_wins,
        "oos_losses": int(oos.get("losses") or 0),
        "wilson_lo": lo,
        "confidence_label": confidence_label(oos_n, lo),
        "last_side": trade_side.value,
        "strict_side": last.side.value,
        "last_confidence": last.confidence,
        "last_reasons": last.reasons[:8],
        "params": params.as_dict(),
        "generations": result.get("generations"),
        "hit_target": result.get("hit_target"),
        "error": None,
    }
    monday = next_monday_open(now)
    if trade_side in {Side.BUY, Side.SELL} and spot > 0:
        calls = puts = None
        chain_exp = None
        if fetch_options:
            try:
                chain_exp, calls, puts = fetch_chain_rows(ticker, monday)
            except Exception:
                calls = puts = None
                chain_exp = None
        conf = last.confidence if last.side != Side.FLAT else max(last.confidence, 0.55)
        card = option_card(
            ticker,
            trade_side,
            spot,
            monday,
            now.date(),
            oos_p,
            conf,
            oos_n,
            lo,
            chain_calls=calls,
            chain_puts=puts,
            chain_expiry=chain_exp,
        )
        card["setup"] = "signal" if last.side != Side.FLAT else "lean"
        row["card"] = card
    else:
        row["card"] = None
    return row


def run_universe(
    settings: Settings | None = None,
    tickers: list[str] | None = None,
    year: int = 2025,
    now: datetime | None = None,
    fetch_options: bool = True,
) -> dict:
    settings = settings or Settings(_env_file=None, trading_mode="paper")
    settings.ensure_data_dir()
    now = now or datetime.now(tz=ET)
    names = [t.upper() for t in (tickers or HIGH_VOLUME)]
    rows: list[dict] = []
    monday = next_monday_open(now)
    hist = settings.data_dir / "history"
    hist.mkdir(parents=True, exist_ok=True)

    def persist() -> dict:
        cards = [r["card"] for r in rows if r.get("card")]
        payload = {
            "generated_at": now.isoformat(),
            "year": year,
            "monday": monday.isoformat(),
            "session": f"Monday {monday.isoformat()} 09:30 ET",
            "note": (
                "Accuracy is Oct–Dec forward-test precision on hourly bars, not a 99% "
                "promise. Monday cards are paper suggestions from the last closed bar."
            ),
            "rows": rows,
            "cards": cards,
            "progress": f"{len(rows)}/{len(names)}",
        }
        (hist / "universe_scan.json").write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
        monday_doc = {
            "generated_at": payload["generated_at"],
            "session": payload["session"],
            "monday": payload["monday"],
            "note": payload["note"],
            "progress": payload["progress"],
            "cards": cards,
            "accuracy": [
                {
                    "ticker": r.get("ticker"),
                    "oos_precision": r.get("oos_precision"),
                    "oos_n": r.get("oos_n"),
                    "train_precision": r.get("train_precision"),
                    "confidence_label": r.get("confidence_label"),
                    "last_side": r.get("last_side"),
                    "error": r.get("error"),
                }
                for r in rows
            ],
        }
        (hist / "monday_signals.json").write_text(json.dumps(monday_doc, default=str, indent=2), encoding="utf-8")
        return payload

    for ticker in names:
        try:
            rows.append(scan_ticker(ticker, settings, year=year, now=now, fetch_options=fetch_options))
        except Exception as exc:  # noqa: BLE001
            rows.append({"ticker": ticker, "error": str(exc), "card": None})
        persist()
        print(f"scanned {ticker} {len(rows)}/{len(names)}", flush=True)
    return persist()


def main() -> None:
    from pa.config import get_settings

    result = run_universe(get_settings(), fetch_options=True)
    print(
        json.dumps(
            {
                "monday": result["monday"],
                "cards": len(result["cards"]),
                "tickers": [
                    {
                        "ticker": r.get("ticker"),
                        "oos": r.get("oos_precision"),
                        "n": r.get("oos_n"),
                        "conf": r.get("confidence_label"),
                        "side": r.get("last_side"),
                        "error": r.get("error"),
                    }
                    for r in result["rows"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
