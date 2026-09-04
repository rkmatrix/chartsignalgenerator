from __future__ import annotations

from pa.config import Settings
from pa.domain.models import AssetClass, Bar, OptionSnapshot, Side, Signal
from pa.quant.features import detect_simple_pattern, fibonacci_levels, rsi_divergence, support_resistance
from pa.quant.indicators import ema, last_valid, macd, resample, rsi, session_vwap
from pa.quant.pricing import probability_itm, theoretical_from_target


class QuantStrategy:
    """P0 directional: 1m/5m/15m EMA alignment + VWAP side + RSI not extreme."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        ticker: str,
        bars_1m: list[Bar],
        option: OptionSnapshot | None = None,
    ) -> Signal:
        ts = bars_1m[-1].ts
        closes = [b.close for b in bars_1m]
        need = max(self.settings.ema_slow, self.settings.rsi_period, 26) + 5
        if len(bars_1m) < need:
            return Signal(
                ticker=ticker,
                side=Side.FLAT,
                confidence=0.0,
                reasons=["insufficient_bars"],
                ts=ts,
            )

        ema_fast = ema(closes, self.settings.ema_fast)
        ema_slow = ema(closes, self.settings.ema_slow)
        vwap = session_vwap(bars_1m)
        rsi_series = rsi(closes, self.settings.rsi_period)
        macd_line, _, macd_hist = macd(closes)

        fast_1 = last_valid(ema_fast)
        slow_1 = last_valid(ema_slow)
        vwap_1 = last_valid(vwap)
        rsi_1 = last_valid(rsi_series)
        last = closes[-1]
        hist = last_valid(macd_hist)

        bars_5 = resample(bars_1m, 5)
        bars_15 = resample(bars_1m, 15)
        fast_5 = last_valid(ema([b.close for b in bars_5], self.settings.ema_fast))
        slow_5 = last_valid(ema([b.close for b in bars_5], self.settings.ema_slow))
        fast_15 = last_valid(ema([b.close for b in bars_15], self.settings.ema_fast))
        slow_15 = last_valid(ema([b.close for b in bars_15], self.settings.ema_slow))

        reasons: list[str] = []
        bull_votes = 0
        bear_votes = 0
        checks = 0

        def vote(cond_bull: bool | None, label: str) -> None:
            nonlocal bull_votes, bear_votes, checks
            if cond_bull is None:
                reasons.append(f"{label}:na")
                return
            checks += 1
            if cond_bull:
                bull_votes += 1
                reasons.append(f"{label}:bull")
            else:
                bear_votes += 1
                reasons.append(f"{label}:bear")

        vote(None if fast_1 is None or slow_1 is None else fast_1 > slow_1, "ema_1m")
        vote(None if fast_5 is None or slow_5 is None else fast_5 > slow_5, "ema_5m")
        vote(None if fast_15 is None or slow_15 is None else fast_15 > slow_15, "ema_15m")
        vote(None if vwap_1 is None else last > vwap_1, "vwap")
        rsi_ok_long = rsi_1 is not None and self.settings.rsi_low <= rsi_1 <= self.settings.rsi_high
        rsi_ok_short = rsi_ok_long
        if rsi_1 is None:
            reasons.append("rsi:na")
        elif not rsi_ok_long:
            reasons.append(f"rsi_extreme:{rsi_1:.1f}")
        else:
            reasons.append(f"rsi_ok:{rsi_1:.1f}")

        if hist is not None:
            reasons.append(f"macd_hist:{hist:.4f}")

        div = rsi_divergence(bars_1m, rsi_series)
        if div:
            reasons.append(f"rsi_div:{div}")
        support, resist = support_resistance(bars_1m)
        if support is not None and resist is not None:
            reasons.append(f"sr:{support:.2f}-{resist:.2f}")
        fibs = fibonacci_levels(bars_1m)
        if fibs:
            reasons.append(f"fib618:{fibs.get('0.618', 0):.2f}")
        pattern = detect_simple_pattern(bars_1m)
        if pattern:
            reasons.append(f"pattern:{pattern}")
        if option and option.iv and option.quote.last:
            years = max(1 / 365, (option.expiry - ts).total_seconds() / (365 * 86400))
            pop = probability_itm(
                last,
                option.strike,
                years,
                option.iv,
                option.right == "call",
            )
            theo = theoretical_from_target(
                last,
                last * 1.01,
                option.strike,
                years,
                option.iv,
                option.right == "call",
            )
            reasons.append(f"pop_itm:{pop:.2f}")
            reasons.append(f"theo_plus1pct:{theo:.2f}")

        side = Side.FLAT
        confidence = 0.0
        if checks == 4 and rsi_ok_long and bull_votes == 4:
            side = Side.BUY
            confidence = 1.0
        elif checks == 4 and rsi_ok_short and bear_votes == 4:
            side = Side.SELL
            confidence = 1.0
        else:
            confidence = max(bull_votes, bear_votes) / 4.0 if checks else 0.0

        occ = option.occ_symbol if option and side != Side.FLAT else None
        asset = AssetClass.OPTION if occ else AssetClass.ETF
        return Signal(
            ticker=ticker,
            side=side,
            confidence=confidence,
            reasons=reasons,
            source="quant",
            ts=ts,
            occ_symbol=occ,
            asset_class=asset,
        )
