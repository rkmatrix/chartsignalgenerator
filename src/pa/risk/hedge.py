from __future__ import annotations

from pa.domain.models import Position


def beta_hedge_note(positions: list[Position]) -> str | None:
    longs = [p for p in positions if p.qty > 0]
    shorts = [p for p in positions if p.qty < 0]
    if len(longs) >= 2 and not shorts:
        return "book is net long index beta — consider a small SPY/QQQ put hedge (paper only)"
    if len(shorts) >= 2 and not longs:
        return "book is net short — consider covering or a call hedge (paper only)"
    return None
