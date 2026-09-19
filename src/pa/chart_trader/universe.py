"""Liquid names used for the multi-ticker chart hunt and Monday board.

SPX is deliberately absent. Its contracts run ~$50, so one of them risks $1,250
at a 25% stop against a $225 cap and risk_block refuses every one — the name
cannot trade, it can only generate signals that die. SPY is the same index at a
tenth the notional, and the scoreboard agrees: SPX -$99, SPY +$266.
"""

HIGH_VOLUME = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "NFLX",
    "AVGO",
    "PLTR",
    "BAC",
    "JPM",
    "XOM",
]
