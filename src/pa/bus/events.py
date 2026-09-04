from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[Any], Awaitable[None] | None]


class EventBus:
    """In-process pub/sub. Swap later for Redis without changing publishers."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    async def publish(self, topic: str, payload: Any) -> None:
        handlers = list(self._handlers.get(topic, ()))
        for handler in handlers:
            result = handler(payload)
            if asyncio.iscoroutine(result):
                await result
