from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pa.clock import MarketClock
from pa.config import Settings
from pa.domain.models import (
    AssetClass,
    KillSource,
    KillSwitchEvent,
    OrderIntent,
    Quote,
    RiskAction,
    RiskDecision,
)


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._drawdown = False
        self.last_event: KillSwitchEvent | None = None

    def file_engaged(self) -> bool:
        return self.path.exists()

    def is_killed(self) -> bool:
        if self.file_engaged() and self.last_event is None:
            self.last_event = KillSwitchEvent(
                source=KillSource.FILE,
                ts=datetime.now(),
                message="kill file present",
            )
        return self.file_engaged() or self._drawdown

    def engage(self, source: KillSource, message: str, ts: datetime) -> KillSwitchEvent:
        event = KillSwitchEvent(source=source, ts=ts, message=message)
        self.last_event = event
        if source == KillSource.DRAWDOWN:
            self._drawdown = True
        if source in {KillSource.FILE, KillSource.API, KillSource.OPERATOR, KillSource.VIX}:
            self.path.write_text(f"{source.value}: {message}\n", encoding="utf-8")
        return event

    def resume(self) -> None:
        """Clear the kill file. Drawdown halt lasts until reset_session()."""
        if self.path.exists():
            self.path.unlink()
        if self.last_event and self.last_event.source != KillSource.DRAWDOWN:
            self.last_event = None

    def reset_session(self) -> None:
        self._drawdown = False
        if not self.file_engaged():
            self.last_event = None


class RiskAgent:
    def __init__(self, settings: Settings, kill: KillSwitch, clock: MarketClock) -> None:
        self.settings = settings
        self.kill = kill
        self.clock = clock

    def size_qty(
        self,
        equity: float,
        price: float,
        asset_class: AssetClass,
        size_mult: float = 1.0,
    ) -> int:
        if equity <= 0 or price <= 0:
            return 0
        cap = equity * (self.settings.max_risk_per_trade_pct / 100.0) * max(0.0, size_mult)
        multiplier = 100 if asset_class == AssetClass.OPTION else 1
        per_unit = price * multiplier
        if per_unit <= 0:
            return 0
        return max(0, int(cap // per_unit))

    def evaluate(
        self,
        intent: OrderIntent,
        *,
        now: datetime,
        equity: float,
        day_pnl_pct: float,
        open_positions: int,
        has_position: bool,
        quote: Quote,
        vix_last: float | None,
        is_entry: bool = True,
        extra_blackout: set[str] | None = None,
        buying_power: float | None = None,
        consecutive_losses: int = 0,
    ) -> RiskDecision:
        if intent.trading_mode.value != "paper":
            return RiskDecision(action=RiskAction.DENY, reasons=["live_blocked"])

        if self.kill.is_killed():
            return RiskDecision(
                action=RiskAction.HALT,
                reasons=["kill_switch"],
                intent=intent,
            )

        if vix_last is not None and vix_last >= self.settings.vix_halt_level:
            self.kill.engage(KillSource.VIX, f"VIX {vix_last}", now)
            return RiskDecision(action=RiskAction.HALT, reasons=[f"vix_halt:{vix_last}"])

        if day_pnl_pct <= -self.settings.daily_drawdown_pct:
            self.kill.engage(
                KillSource.DRAWDOWN,
                f"daily drawdown {day_pnl_pct:.2f}%",
                now,
            )
            return RiskDecision(
                action=RiskAction.HALT,
                reasons=[f"daily_drawdown:{day_pnl_pct:.2f}"],
            )

        if not is_entry:
            return RiskDecision(action=RiskAction.ALLOW, reasons=["exit"], sized_qty=intent.qty, intent=intent)

        skip = self.clock.skip_reason(now)
        if skip:
            return RiskDecision(action=RiskAction.DENY, reasons=[f"session:{skip}"], intent=intent)

        blocked = set(self.settings.blackout_tickers)
        if extra_blackout:
            blocked |= {t.upper() for t in extra_blackout}
        if intent.ticker.upper() in blocked:
            return RiskDecision(action=RiskAction.DENY, reasons=["earnings_blackout"], intent=intent)

        if not has_position and open_positions >= self.settings.max_open_positions:
            return RiskDecision(action=RiskAction.DENY, reasons=["max_open_positions"], intent=intent)

        price = quote.mid if quote.mid > 0 else quote.last
        size_mult = 1.0
        reasons = ["ok"]
        if vix_last is not None and vix_last >= self.settings.vix_size_down_level:
            size_mult *= self.settings.vix_size_down_mult
            reasons.append(f"vix_size_down:{vix_last}")
        if consecutive_losses >= self.settings.streak_reduce_after:
            size_mult *= self.settings.streak_size_mult
            reasons.append(f"streak_down:{consecutive_losses}")
        sized = self.size_qty(equity, price, intent.asset_class, size_mult=size_mult)
        if sized <= 0:
            return RiskDecision(action=RiskAction.DENY, reasons=["size_zero"], intent=intent)

        notional = sized * price * (100 if intent.asset_class == AssetClass.OPTION else 1)
        if buying_power is not None and notional > buying_power:
            return RiskDecision(action=RiskAction.DENY, reasons=["buying_power"], intent=intent)

        sized = min(sized, intent.qty)
        updated = intent.model_copy(update={"qty": sized})
        return RiskDecision(
            action=RiskAction.ALLOW,
            reasons=reasons,
            sized_qty=sized,
            intent=updated,
        )
