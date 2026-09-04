from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ChartParams:
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_low: float = 40.0
    rsi_high: float = 65.0
    min_votes: int = 4
    require_macd: bool = True
    require_vwap: bool = True
    min_sep: float = 0.0
    horizon: int = 3
    cooldown: int = 3
    ht_fast: int = 4
    ht_slow: int = 7

    def as_dict(self) -> dict:
        return asdict(self)
