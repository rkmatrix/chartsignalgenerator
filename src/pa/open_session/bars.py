from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pa.domain.models import Bar, Timeframe

ET = ZoneInfo("America/New_York")

# Cash index for 1m bars. Options still print as SPX.
YF_SYMBOL = {"SPX": "^GSPC"}


def yf_symbol(ticker: str) -> str:
    return YF_SYMBOL.get(ticker.upper(), ticker.upper())


def load_intraday_1m(ticker: str, data_dir: Path) -> tuple[list[Bar], str, float | None]:
    ticker = ticker.upper()
    cache = data_dir / "history" / f"{ticker}_1m_today.json"
    try:
        import json

        if cache.exists():
            age = datetime.now().timestamp() - cache.stat().st_mtime
            if age < 25:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                rows, prior = _unpack_cache(payload)
                bars, prior_close = _today_and_prior(ticker, rows, prior)
                return bars, "cache", prior_close
    except Exception:
        pass
    try:
        import json

        import yfinance as yf

        frame = yf.download(
            yf_symbol(ticker),
            period="2d",
            interval="1m",
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=True,
        )
        if frame is None or frame.empty:
            raise RuntimeError("empty")
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame.columns = frame.columns.get_level_values(0)
        rows = []
        for idx, row in frame.iterrows():
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            rows.append(
                {
                    "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume") or 0),
                }
            )
        cache.parent.mkdir(parents=True, exist_ok=True)
        bars, prior_close = _today_and_prior(ticker, rows, None)
        cache.write_text(
            json.dumps({"bars": rows, "prior_close": prior_close}),
            encoding="utf-8",
        )
        return bars, "yfinance", prior_close
    except Exception:
        return [], "none", None


def _unpack_cache(payload) -> tuple[list[dict], float | None]:
    if isinstance(payload, list):
        return payload, None
    return list(payload.get("bars") or []), payload.get("prior_close")


def _today_and_prior(
    ticker: str, rows: list[dict], cached_prior: float | None
) -> tuple[list[Bar], float | None]:
    bars = _rows_to_bars(ticker, rows)
    if not bars:
        return [], cached_prior
    by_day: dict = {}
    for bar in bars:
        by_day.setdefault(bar.ts.date(), []).append(bar)
    days = sorted(by_day)
    prior_close = cached_prior
    if len(days) >= 2:
        from pa.open_session.levels import split_session

        _, prev_rth = split_session(by_day[days[-2]])
        if prev_rth:
            prior_close = prev_rth[-1].close
    return by_day[days[-1]], prior_close


def _rows_to_bars(ticker: str, rows: list[dict]) -> list[Bar]:
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
                ticker=ticker.upper(),
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0),
                timeframe=Timeframe.M1,
            )
        )
    bars.sort(key=lambda b: b.ts)
    return bars
