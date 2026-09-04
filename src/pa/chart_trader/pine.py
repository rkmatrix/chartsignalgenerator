from __future__ import annotations

from pa.chart_trader.params import ChartParams


def to_pine(params: ChartParams, ticker: str = "SPY") -> str:
    macd_clause = "and macdHist > 0" if params.require_macd else ""
    vwap_clause = "and close > vwap" if params.require_vwap else ""
    return f"""//@version=5
indicator("PA ChartTrader {ticker}", overlay=true)
emaFast = ta.ema(close, {params.ema_fast})
emaSlow = ta.ema(close, {params.ema_slow})
rsi = ta.rsi(close, {params.rsi_period})
vwap = ta.vwap(hlc3)
[macdLine, signal, macdHist] = ta.macd(close, 12, 26, 9)
bull = emaFast > emaSlow {vwap_clause} {macd_clause} and rsi >= {params.rsi_low} and rsi <= {params.rsi_high}
bear = emaFast < emaSlow and rsi >= {params.rsi_low} and rsi <= {params.rsi_high}
plot(emaFast, "EMA fast", color=color.teal)
plot(emaSlow, "EMA slow", color=color.orange)
plot(vwap, "VWAP", color=color.gray)
plotshape(bull, title="Buy", style=shape.triangleup, location=location.belowbar, color=color.green, size=size.tiny)
plotshape(bear, title="Sell", style=shape.triangledown, location=location.abovebar, color=color.red, size=size.tiny)
"""


def tradingview_url(ticker: str = "SPY") -> str:
    name = ticker.upper()
    if name == "SPX":
        symbol = "SP:SPX"
    elif name in {"SPY", "QQQ", "IWM", "DIA"}:
        symbol = f"AMEX:{name}"
    else:
        symbol = f"NASDAQ:{name}"
    return f"https://www.tradingview.com/chart/?symbol={symbol}"
