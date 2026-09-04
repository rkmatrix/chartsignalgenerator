from __future__ import annotations

from pa.domain.models import AgentScore, Fill, Signal, Side


class Scorekeeper:
    """Observational hit rates. Never auto-raises size from recent P&L."""

    def __init__(self) -> None:
        self.scores: dict[str, AgentScore] = {}
        self._open: dict[str, Signal] = {}

    def on_signal(self, signal: Signal) -> None:
        row = self.scores.setdefault(signal.source, AgentScore(source=signal.source))
        row.signals += 1
        row.avg_confidence = (
            (row.avg_confidence * (row.signals - 1) + signal.confidence) / row.signals
        )
        if signal.side != Side.FLAT:
            self._open[f"{signal.source}:{signal.ticker}"] = signal

    def on_fill_close(self, fill: Fill, pnl: float) -> None:
        for key, signal in list(self._open.items()):
            if signal.ticker != fill.ticker:
                continue
            row = self.scores.setdefault(signal.source, AgentScore(source=signal.source))
            if pnl > 0:
                row.wins += 1
            elif pnl < 0:
                row.losses += 1
            decided = row.wins + row.losses
            row.hit_rate = row.wins / decided if decided else 0.0
            del self._open[key]
            break

    def snapshot(self) -> list[AgentScore]:
        return [s.model_copy() for s in self.scores.values()]
