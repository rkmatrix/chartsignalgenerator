from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes(
    spot: float,
    strike: float,
    years: float,
    rate: float,
    iv: float,
    call: bool,
) -> float:
    if spot <= 0 or strike <= 0 or years <= 0 or iv <= 0:
        return max(0.0, (spot - strike) if call else (strike - spot))
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    d2 = d1 - iv * math.sqrt(years)
    if call:
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * years) * _norm_cdf(d2)
    return strike * math.exp(-rate * years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def theoretical_from_target(
    spot: float,
    target: float,
    strike: float,
    years: float,
    iv: float,
    call: bool,
    rate: float = 0.04,
) -> float:
    return black_scholes(target, strike, years, rate, iv, call)


def probability_itm(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    call: bool,
    rate: float = 0.04,
) -> float:
    """N(d2) / N(-d2) — probability of finishing ITM, not a guaranteed win rate."""
    if spot <= 0 or strike <= 0 or years <= 0 or iv <= 0:
        return 0.5
    d2 = (math.log(spot / strike) + (rate - 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    return _norm_cdf(d2) if call else _norm_cdf(-d2)
