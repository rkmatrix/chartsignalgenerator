from __future__ import annotations

from datetime import datetime

from pa.chart_trader.forward import forward_test
from pa.chart_trader.params import ChartParams
from pa.domain.models import Bar

TARGET = 0.99
MIN_TRADES = 20


def _naive(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo else ts


def split_year(bars: list[Bar], year: int) -> tuple[int, int, int]:
    """Indices: warmup start, Oct 1 (train end / OOS start), end."""
    cut = datetime(year, 10, 1)
    train_end = len(bars)
    start = 0
    for i, bar in enumerate(bars):
        ts = _naive(bar.ts)
        if ts.year == year and start == 0:
            start = i
        if ts >= cut:
            train_end = i
            break
    return max(start, 40), train_end, len(bars)


def compact_grid() -> list[ChartParams]:
    """Small neighborhood around the 2025 SPY hunt winner — used for multi-ticker scans."""
    out: list[ChartParams] = []
    for rlo, rhi in ((50.0, 55.0), (45.0, 60.0), (40.0, 65.0)):
        for votes in (3, 4):
            for macd in (True, False):
                for sep in (0.0005, 0.001):
                    out.append(
                        ChartParams(
                            ema_fast=13,
                            ema_slow=21,
                            rsi_low=rlo,
                            rsi_high=rhi,
                            min_votes=votes,
                            require_macd=macd,
                            require_vwap=True,
                            min_sep=sep,
                            horizon=3,
                            cooldown=3,
                        )
                    )
    return out


def _grid() -> list[ChartParams]:
    out: list[ChartParams] = []
    for fast in (8, 9, 13):
        for slow in (21, 34):
            if fast >= slow:
                continue
            for rlo, rhi in ((40.0, 65.0), (45.0, 60.0), (35.0, 70.0)):
                for votes in (3, 4, 5):
                    for macd in (True, False):
                        for horizon in (2, 3, 6):
                            for sep in (0.0, 0.0005):
                                out.append(
                                    ChartParams(
                                        ema_fast=fast,
                                        ema_slow=slow,
                                        rsi_low=rlo,
                                        rsi_high=rhi,
                                        min_votes=votes,
                                        require_macd=macd,
                                        require_vwap=True,
                                        horizon=horizon,
                                        cooldown=horizon,
                                        min_sep=sep,
                                    )
                                )
    return out


def _neighbors(base: ChartParams) -> list[ChartParams]:
    tweaks = []
    for votes in {max(3, base.min_votes - 1), base.min_votes, min(6, base.min_votes + 1)}:
        for rlo in {max(30.0, base.rsi_low - 5), base.rsi_low, min(50.0, base.rsi_low + 5)}:
            for rhi in {max(55.0, base.rsi_high - 5), base.rsi_high, min(80.0, base.rsi_high + 5)}:
                for sep in {0.0, 0.0005, 0.001, base.min_sep}:
                    if rlo >= rhi:
                        continue
                    tweaks.append(
                        ChartParams(
                            **{
                                **base.as_dict(),
                                "min_votes": votes,
                                "rsi_low": rlo,
                                "rsi_high": rhi,
                                "min_sep": sep,
                                "require_macd": True,
                            }
                        )
                    )
    # unique
    seen: set[tuple] = set()
    uniq: list[ChartParams] = []
    for p in tweaks:
        key = tuple(p.as_dict().values())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def hunt(
    ticker: str,
    bars: list[Bar],
    year: int = 2025,
    target: float = TARGET,
    min_trades: int = MIN_TRADES,
    compact: bool = False,
) -> dict:
    train_start, train_end, stop = split_year(bars, year)
    generations = 0
    scored: list[dict] = []
    best: dict | None = None
    best_params: ChartParams | None = None
    grid = compact_grid() if compact else _grid()

    def consider(params: ChartParams) -> dict:
        nonlocal generations, best, best_params
        generations += 1
        train = forward_test(ticker, bars, params, start=train_start, end=max(train_start + 10, train_end - params.horizon))
        row = {
            "params": params.as_dict(),
            "train_precision": train["precision"],
            "train_n": train["n"],
            "train_wins": train["wins"],
            "train_losses": train["losses"],
            "generation": generations,
        }
        if train["n"] < min_trades:
            return row
        scored.append(row)
        if best is None or (train["precision"], train["n"]) > (best["train_precision"], best["train_n"]):
            best = row
            best_params = params
        return row

    for params in grid:
        row = consider(params)
        if row["train_n"] >= min_trades and row["train_precision"] >= target:
            break

    if (not compact) and best_params is not None and (best or {}).get("train_precision", 0) < target:
        for params in _neighbors(best_params):
            row = consider(params)
            if row["train_n"] >= min_trades and row["train_precision"] >= target:
                break

    scored.sort(key=lambda r: (r["train_precision"], r["train_n"]), reverse=True)
    oos = None
    oos_samples: list = []
    hit_target = False
    if best_params is not None:
        oos_end = min(len(bars) - best_params.horizon, stop - best_params.horizon)
        if oos_end - train_end >= 10:
            oos_run = forward_test(
                ticker,
                bars,
                best_params,
                start=train_end,
                end=oos_end,
            )
            oos = {
                "precision": oos_run["precision"],
                "n": oos_run["n"],
                "wins": oos_run["wins"],
                "losses": oos_run["losses"],
            }
            oos_samples = oos_run["samples"]
            hit_target = oos_run["precision"] >= target and oos_run["n"] >= max(5, min_trades // 4)
        else:
            oos = {"precision": 0.0, "n": 0, "wins": 0, "losses": 0, "reason": "no_oos_slice"}

    return {
        "ticker": ticker,
        "year": year,
        "bars": len(bars),
        "train_range": [train_start, train_end],
        "target": target,
        "min_trades": min_trades,
        "generations": generations,
        "candidates_kept": len(scored),
        "hit_target": hit_target,
        "best": best,
        "oos": oos,
        "top": scored[:12],
        "samples": oos_samples,
        "note": (
            "Guesses use only bars up to time t. The Oct–Dec 2025 slice is a true "
            "forward test. 99% OOS with a useful number of day trades is not a "
            "realistic market property; the loop stops if it hits the target or "
            "the grid is exhausted."
        ),
    }
