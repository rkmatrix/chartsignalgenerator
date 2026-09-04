from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"


class AssetClass(StrEnum):
    ETF = "etf"
    OPTION = "option"


class TradingMode(StrEnum):
    PAPER = "paper"


class RiskAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HALT = "halt"


class KillSource(StrEnum):
    FILE = "file"
    API = "api"
    DRAWDOWN = "drawdown"
    OPERATOR = "operator"
    VIX = "vix"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"


def new_client_order_id() -> str:
    return uuid4().hex


class Bar(BaseModel):
    ticker: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: Timeframe = Timeframe.M1

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.upper()


class Quote(BaseModel):
    ticker: str
    ts: datetime
    bid: float
    ask: float
    last: float
    bid_size: float = 0.0
    ask_size: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)


class OptionSnapshot(BaseModel):
    occ_symbol: str
    underlying: str
    expiry: datetime
    strike: float
    right: Literal["call", "put"]
    quote: Quote
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    iv: float | None = None
    open_interest: float | None = None


class Signal(BaseModel):
    ticker: str
    side: Side
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    source: str = "quant"
    ts: datetime
    occ_symbol: str | None = None
    asset_class: AssetClass = AssetClass.ETF


class OrderIntent(BaseModel):
    client_order_id: str = Field(default_factory=new_client_order_id)
    ticker: str
    qty: int = Field(gt=0)
    side: Side
    asset_class: AssetClass
    trading_mode: TradingMode = TradingMode.PAPER
    occ_symbol: str | None = None
    limit_hint: float | None = None
    stop_price: float | None = None
    ts: datetime
    reason: str = ""

    @field_validator("trading_mode")
    @classmethod
    def _paper_only(cls, value: TradingMode) -> TradingMode:
        if value != TradingMode.PAPER:
            raise ValueError("OrderIntent.trading_mode must be paper")
        return value

    @field_validator("side")
    @classmethod
    def _no_flat(cls, value: Side) -> Side:
        if value == Side.FLAT:
            raise ValueError("OrderIntent side cannot be flat")
        return value


class RiskDecision(BaseModel):
    action: RiskAction
    reasons: list[str] = Field(default_factory=list)
    sized_qty: int = 0
    intent: OrderIntent | None = None


class Fill(BaseModel):
    client_order_id: str
    ticker: str
    qty: int
    price: float
    side: Side
    expected_mid: float
    slippage: float
    ts: datetime
    occ_symbol: str | None = None
    asset_class: AssetClass


class Position(BaseModel):
    ticker: str
    qty: int
    avg_price: float
    asset_class: AssetClass
    occ_symbol: str | None = None
    stop_price: float | None = None
    trail_stop: float | None = None
    high_water: float | None = None
    scale_out_done: bool = False
    opened_at: datetime | None = None
    multiplier: int = 1
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    expiry: datetime | None = None

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.avg_price * self.multiplier


class KillSwitchEvent(BaseModel):
    source: KillSource
    ts: datetime
    message: str


class JournalEvent(BaseModel):
    topic: str
    payload: dict[str, Any]
    ts: datetime


class CalendarEvent(BaseModel):
    name: str
    ts: datetime
    kind: str
    tickers: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    headline: str
    source: str
    ts: datetime
    tickers: list[str] = Field(default_factory=list)
    sentiment: float = 0.0
    risk_off: bool = False


class FlowPrint(BaseModel):
    ticker: str
    ts: datetime
    side: str
    size: float
    premium: float | None = None
    note: str = ""


class Level2Book(BaseModel):
    ticker: str
    ts: datetime
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


class AgentScore(BaseModel):
    source: str
    signals: int = 0
    wins: int = 0
    losses: int = 0
    hit_rate: float = 0.0
    avg_confidence: float = 0.0


class WashSaleFlag(BaseModel):
    ticker: str
    message: str
    ts: datetime


class PatchRequest(BaseModel):
    ts: datetime
    error: str
    suggestion: str
    applied: bool = False
