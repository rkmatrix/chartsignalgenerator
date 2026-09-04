from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from pa.clock import RTH_OPEN, MarketClock

ET = ZoneInfo("America/New_York")


def minutes_since_open(now: datetime) -> float | None:
    local = now.astimezone(ET)
    if local.time().replace(tzinfo=None) < RTH_OPEN:
        return None
    open_dt = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return (local - open_dt).total_seconds() / 60.0


def minutes_until_close(now: datetime, clock: MarketClock) -> int | None:
    if not clock.is_open(now):
        return None
    local = now.astimezone(ET)
    close_t = clock.session_close_time(local.date())
    close_dt = local.replace(hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0)
    return int(max(0, (close_dt - local).total_seconds() // 60))


def session_phase(now: datetime, clock: MarketClock, orb_minutes: int = 15) -> str:
    """Where we are in the playbook. hunt = allowed to fire a strong signal."""
    skip = clock.skip_reason(now)
    if skip == "weekend":
        return "weekend"
    if skip == "holiday":
        return "holiday"
    if skip == "session_closed":
        local = now.astimezone(ET).time().replace(tzinfo=None)
        if time(4, 0) <= local < RTH_OPEN:
            return "premarket"
        return "closed"
    elapsed = minutes_since_open(now)
    if elapsed is None:
        return "premarket"
    if elapsed < orb_minutes:
        return "orb_building"
    if elapsed < orb_minutes + 2:
        return "confirming"
    if skip == "lunch":
        return "lunch"
    if skip == "close_buffer":
        return "late"
    if skip == "open_buffer":
        # PA open buffer matches ORB window; still hunt once ORB+2 bars exist.
        if elapsed >= orb_minutes + 2:
            return "hunt"
        return "orb_building"
    return "hunt"
