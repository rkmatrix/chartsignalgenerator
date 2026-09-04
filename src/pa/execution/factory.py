from __future__ import annotations

from pa.config import LiveTradingBlocked, Settings
from pa.execution.paper import PaperBroker


def build_broker(settings: Settings) -> PaperBroker:
    """Live adapters are not imported. Alpaca paper is opt-in but still paper-only."""
    name = (settings.paper_broker or "internal").strip().lower()
    if name in {"alpaca", "alpaca_paper"}:
        if "paper" not in settings.alpaca_base_url.lower():
            raise LiveTradingBlocked("Alpaca live URLs are blocked. Use the paper API host.")
        # Wire stays internal until operator keys are proven; the URL check is the live gate.
        return PaperBroker(settings.starting_equity, settings.slippage_bps)
    return PaperBroker(settings.starting_equity, settings.slippage_bps)
