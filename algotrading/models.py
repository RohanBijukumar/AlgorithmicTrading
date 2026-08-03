from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float | None
    volume: int
    provider: str
    interval: str = "1d"
    price_basis: str = "close"


@dataclass(frozen=True)
class FetchResult:
    provider: str
    symbol: str
    provider_symbol: str
    requested_start: date
    requested_end: date
    bars: list[MarketBar]
    fetched_at: datetime
    success: bool
    error_message: str | None = None


@dataclass(frozen=True)
class Trade:
    id: int
    portfolio_id: int
    symbol: str
    side: str
    shares: int
    execution_date: date
    execution_price: float
    cash_impact: float
    created_at: datetime
    fees: float = 0.0
    slippage: float = 0.0
