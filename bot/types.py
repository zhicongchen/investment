from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class AccountSnapshot:
    account: str
    net_liquidation: Optional[float] = None
    buying_power: Optional[float] = None
    currency: str = "USD"


@dataclass(frozen=True)
class Position:
    account: str
    ticker: str
    quantity: float
    average_cost: Optional[float] = None
    market_price: Optional[float] = None
    market_value: Optional[float] = None
    sec_type: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"


@dataclass(frozen=True)
class ContractSummary:
    symbol: str
    sec_type: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"
    con_id: Optional[int] = None
    primary_exchange: Optional[str] = None


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    last_price: Optional[float] = None
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class DailyBar:
    date: datetime
    close: float


@dataclass(frozen=True)
class StrategyContext:
    account: Optional[AccountSnapshot]
    positions: dict[str, Position]
    market_data: dict[str, MarketSnapshot]
    daily_history: dict[str, list[DailyBar]]
    now: datetime
    config: object


@dataclass(frozen=True)
class OrderIntent:
    ticker: str
    side: str
    quantity: float
    order_type: str
    limit_price: float
    time_in_force: str = "DAY"
    reason: str = ""

    @property
    def estimated_notional(self) -> float:
        return round(self.quantity * self.limit_price, 2)


@dataclass(frozen=True)
class RiskResult:
    allowed: bool
    reasons: list[str]


@dataclass(frozen=True)
class BrokerStatus:
    connected: bool
    message: str = ""


@dataclass(frozen=True)
class SubmitResult:
    order_id: Optional[int]
    perm_id: Optional[int]
    status: str
    filled: float = 0
    remaining: float = 0
    average_fill_price: Optional[float] = None
    raw_message: str = ""


@dataclass(frozen=True)
class Fill:
    execution_id: str
    order_id: Optional[int]
    ticker: str
    side: str
    shares: float
    price: float
    time: Optional[datetime] = None
