from __future__ import annotations

from pa.config import LiveTradingBlocked, Settings
from pa.execution.paper import PaperBroker


def build_broker(settings: Settings) -> PaperBroker:
    """The equity loop stays on the internal paper book.

    Webull paper orders are placed by the open-session ledger, which is the
    desk that actually opens and closes option signals. This builder never
    talks to a live broker.
    """
    name = (settings.paper_broker or "internal").strip().lower()
    if name in {"alpaca", "alpaca_paper"}:
        if "paper" not in settings.alpaca_base_url.lower():
            raise LiveTradingBlocked("Alpaca live URLs are blocked. Use the paper API host.")
        # Wire stays internal until operator keys are proven; the URL check is the live gate.
        return PaperBroker(settings.starting_equity, settings.slippage_bps)
    return PaperBroker(settings.starting_equity, settings.slippage_bps)
