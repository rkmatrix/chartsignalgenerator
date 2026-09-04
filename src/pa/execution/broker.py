from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pa.domain.models import Fill, OrderIntent, Position, Quote


class Broker(Protocol):
    def submit(self, intent: OrderIntent, quote: Quote, now: datetime) -> Fill: ...

    def flatten_all(self, quotes: dict[str, Quote], now: datetime) -> list[Fill]: ...

    def snapshot(self) -> list[Position]: ...

    def equity(self) -> float: ...
