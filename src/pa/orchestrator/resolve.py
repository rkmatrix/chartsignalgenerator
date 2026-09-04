from __future__ import annotations

from pa.domain.models import Signal, Side


class SignalInbox:
    def __init__(self) -> None:
        self._items: list[Signal] = []

    def push(self, signal: Signal) -> None:
        self._items.append(signal)

    def drain(self) -> list[Signal]:
        items = self._items
        self._items = []
        return items


def resolve(signals: list[Signal]) -> Signal | None:
    """Highest confidence non-flat signal. Observational scores do not boost weights."""
    actionable = [s for s in signals if s.side != Side.FLAT]
    if not actionable:
        return None
    return max(actionable, key=lambda s: (s.confidence, s.ts.timestamp()))
