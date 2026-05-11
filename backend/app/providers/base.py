from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class CompanyProfile:
    ticker: str
    name: str = ""
    sector: str = ""
    exchange: str = ""


@dataclass(frozen=True)
class PriceBarData:
    date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    adjusted_close: Optional[float]


@dataclass(frozen=True)
class FundamentalsData:
    date: date
    pe: Optional[float] = None
    forward_pe: Optional[float] = None
    eps_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    debt_to_equity: Optional[float] = None
    free_cash_flow: Optional[float] = None
    dividend_yield: Optional[float] = None


class MarketDataProvider(ABC):
    @abstractmethod
    def get_company_profile(self, ticker: str) -> CompanyProfile:
        raise NotImplementedError

    @abstractmethod
    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        raise NotImplementedError

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        raise NotImplementedError
