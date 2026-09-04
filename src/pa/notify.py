from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

log = logging.getLogger("pa.notify")


async def post_webhook(url: str, topic: str, payload: dict[str, Any], ts: datetime) -> bool:
    if not url.strip():
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url.strip(),
                json={"topic": topic, "ts": ts.isoformat(), "payload": payload},
            )
            return resp.status_code < 300
    except Exception as exc:
        log.warning("webhook failed: %s", exc)
        return False
