from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.domain.models import Bar, Timeframe

ET = ZoneInfo("America/New_York")


def _to_bars(ticker: str, rows: list[dict], timeframe: Timeframe) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        ts = row["ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        else:
            ts = ts.astimezone(ET)
        bars.append(
            Bar(
                ticker=ticker,
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0),
                timeframe=timeframe,
            )
        )
    bars.sort(key=lambda b: b.ts)
    return bars


def cache_path(data_dir: Path, ticker: str, year: int, interval: str) -> Path:
    return data_dir / "history" / f"{ticker.upper()}_{interval}_{year}.json"


def save_cache(path: Path, bars: list[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "ts": b.ts.isoformat(),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_cache(path: Path, ticker: str, timeframe: Timeframe) -> list[Bar] | None:
    if not path.exists():
        return None
    rows = json.loads(path.read_text(encoding="utf-8"))
    return _to_bars(ticker, rows, timeframe)


def load_yfinance(ticker: str, year: int, interval: str = "1h") -> list[Bar]:
    import yfinance as yf

    start = f"{year}-01-01"
    end = f"{year + 1}-01-01"
    frame = yf.download(
        ticker,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if frame is None or frame.empty:
        raise RuntimeError(f"yfinance returned no {interval} bars for {ticker} {year}")
    if getattr(frame.columns, "nlevels", 1) > 1:
        frame.columns = frame.columns.get_level_values(0)
    rows = []
    for idx, row in frame.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        rows.append(
            {
                "ts": ts,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume") or 0),
            }
        )
    tf = Timeframe.M15 if interval == "15m" else Timeframe.M5 if interval == "5m" else Timeframe.M1
    if interval == "1h":
        tf = Timeframe.M15
    return _to_bars(ticker, rows, tf)


def load_year_bars(
    ticker: str,
    year: int,
    data_dir: Path,
    interval: str = "1h",
    polygon_key: str = "",
) -> tuple[list[Bar], str]:
    path = cache_path(data_dir, ticker, year, interval)
    cached = load_cache(path, ticker, Timeframe.M15)
    if cached and len(cached) > 50:
        return cached, "cache"
    if polygon_key.strip() and interval in {"1m", "5m", "15m", "1h"}:
        try:
            bars = _load_polygon(ticker, year, interval, polygon_key)
            save_cache(path, bars)
            return bars, "polygon"
        except Exception:
            pass
    bars = load_yfinance(ticker, year, interval=interval)
    save_cache(path, bars)
    return bars, "yfinance"


def _load_polygon(ticker: str, year: int, interval: str, api_key: str) -> list[Bar]:
    import httpx

    mult, span = {
        "1m": (1, "minute"),
        "5m": (5, "minute"),
        "15m": (15, "minute"),
        "1h": (1, "hour"),
    }[interval]
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker.upper()}/range/"
        f"{mult}/{span}/{year}-01-01/{year}-12-31"
    )
    rows: list[dict] = []
    with httpx.Client(timeout=30.0) as client:
        payload = client.get(
            url,
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key},
        )
        payload.raise_for_status()
        body = payload.json()
    for row in body.get("results") or []:
        rows.append(
            {
                "ts": datetime.fromtimestamp(row["t"] / 1000, tz=ET),
                "open": row["o"],
                "high": row["h"],
                "low": row["l"],
                "close": row["c"],
                "volume": row.get("v") or 0,
            }
        )
    if not rows:
        raise RuntimeError("polygon empty")
    return _to_bars(ticker, rows, Timeframe.M15)
