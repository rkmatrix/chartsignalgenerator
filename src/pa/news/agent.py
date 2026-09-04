from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pa.domain.models import NewsItem, Signal, Side

ET = ZoneInfo("America/New_York")

_POS = ("beat", "upgrade", "approved", "record", "surge", "rally", "bullish")
_NEG = ("miss", "downgrade", "reject", "probe", "war", "crash", "bankrupt", "bearish")
_RISK_OFF = ("war", "missile", "default", "bank run", "emergency rate")


def score_headline(text: str) -> tuple[float, bool]:
    low = text.lower()
    score = 0.0
    for w in _POS:
        if w in low:
            score += 0.25
    for w in _NEG:
        if w in low:
            score -= 0.25
    risk_off = any(w in low for w in _RISK_OFF)
    return max(-1.0, min(1.0, score)), risk_off


class NewsAgent:
    """Fixture headlines plus optional NewsAPI if NEWS_API_KEY is set."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key.strip()
        self._cache: list[NewsItem] = []

    def fixtures(self, now: datetime, tickers: list[str]) -> list[NewsItem]:
        items = [
            NewsItem(
                headline="Index futures steady ahead of session",
                source="fixture",
                ts=now,
                tickers=tickers,
                sentiment=0.05,
            ),
            NewsItem(
                headline="Fed speakers urge patience on policy",
                source="fixture",
                ts=now,
                tickers=["SPY"],
                sentiment=0.0,
            ),
        ]
        self._cache = items
        return items

    async def headlines(self, now: datetime, tickers: list[str]) -> list[NewsItem]:
        if not self.api_key:
            return self.fixtures(now, tickers)
        try:
            import httpx

            query = " OR ".join(tickers)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://newsapi.org/v2/everything",
                    params={"q": query, "pageSize": 5, "language": "en", "apiKey": self.api_key},
                )
                resp.raise_for_status()
                payload = resp.json()
            items: list[NewsItem] = []
            for art in payload.get("articles") or []:
                title = art.get("title") or ""
                sent, risk = score_headline(title)
                items.append(
                    NewsItem(
                        headline=title,
                        source=art.get("source", {}).get("name") or "newsapi",
                        ts=now,
                        tickers=tickers,
                        sentiment=sent,
                        risk_off=risk,
                    )
                )
            self._cache = items or self.fixtures(now, tickers)
            return self._cache
        except Exception:
            return self.fixtures(now, tickers)

    def to_signals(self, items: list[NewsItem], now: datetime) -> list[Signal]:
        signals: list[Signal] = []
        for item in items:
            if item.risk_off:
                for ticker in item.tickers or ["SPY"]:
                    signals.append(
                        Signal(
                            ticker=ticker,
                            side=Side.FLAT,
                            confidence=0.8,
                            reasons=["news_risk_off", item.headline[:80]],
                            source="news",
                            ts=now,
                        )
                    )
        return signals
