from __future__ import annotations

from dataclasses import dataclass

from pa.open_session.setups import SOLO_STRATEGIES, Candidate


@dataclass
class FusedSignal:
    ticker: str
    direction: str
    strategies: list[str]
    families: list[str]
    conviction: float
    trigger: float | None
    stop: float | None
    thesis: str
    last: float
    vetoed: bool = False
    veto_reason: str = ""
    playbook: str = ""
    window: str = ""


def fuse(candidates: list[Candidate], spy_bias: str | None = None) -> FusedSignal | None:
    """Two different families on the same side, or a solo gap/momentum burst.

    Premarket + ORB both count as 'level' so they cannot form confluence alone.
    """
    if not candidates:
        return None
    ticker = candidates[0].ticker
    calls = [c for c in candidates if c.direction == "call"]
    puts = [c for c in candidates if c.direction == "put"]
    if calls and puts:
        return FusedSignal(
            ticker=ticker,
            direction="flat",
            strategies=sorted({c.strategy for c in candidates}),
            families=[],
            conviction=0.0,
            trigger=None,
            stop=None,
            thesis="call and put both fired — cancelled",
            last=candidates[0].last,
            vetoed=True,
            veto_reason="opposing",
        )
    side = calls or puts
    families = {c.family for c in side}
    has_solo = any(c.strategy in SOLO_STRATEGIES for c in side)
    # Premarket + ORB are both "level" and cannot confluence alone.
    # Gap and an ATR-sized momentum burst may stand alone (same idea as SignalValidator).
    if len(families) < 2 and not has_solo:
        return None
    direction = side[0].direction
    best_per_family: dict[str, Candidate] = {}
    for cand in side:
        prev = best_per_family.get(cand.family)
        if prev is None or cand.conviction > prev.conviction:
            best_per_family[cand.family] = cand
    conviction = round(sum(c.conviction for c in best_per_family.values()), 2)
    if spy_bias == "bearish" and direction == "call" and conviction < 2.5:
        return None
    if spy_bias == "bullish" and direction == "put" and conviction < 2.5:
        return None
    best = max(side, key=lambda c: c.conviction)
    return FusedSignal(
        ticker=ticker,
        direction=direction,
        strategies=sorted({c.strategy for c in side}),
        families=sorted(families),
        conviction=conviction,
        trigger=best.trigger,
        stop=best.stop,
        thesis=" · ".join(c.thesis for c in side),
        last=best.last,
    )


def pick_strongest(ideas: list[FusedSignal]) -> FusedSignal | None:
    live = [i for i in ideas if not i.vetoed and i.direction in {"call", "put"}]
    if not live:
        return None
    live.sort(key=lambda i: (i.conviction, len(i.families)), reverse=True)
    return live[0]
