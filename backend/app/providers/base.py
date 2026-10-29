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


# Income-statement and cash-flow lines are period flows; balance-sheet lines are period-end values.
# Cash outflows (capital_expenditure, dividends_paid, share_repurchases) and interest_expense are
# stored as positive magnitudes so the analysis never has to guess a provider's sign convention.
STATEMENT_FLOW_FIELDS = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "ebitda",
    "interest_expense",
    "pretax_income",
    "income_tax",
    "net_income",
    "diluted_eps",
    "operating_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
    "dividends_paid",
    "share_repurchases",
    "stock_based_compensation",
    "depreciation",
)
STATEMENT_BALANCE_FIELDS = (
    "diluted_shares",
    "shares_outstanding",
    "total_assets",
    "current_assets",
    "cash_and_investments",
    "receivables",
    "inventory",
    "total_liabilities",
    "current_liabilities",
    "total_debt",
    "long_term_debt",
    "shareholders_equity",
    "retained_earnings",
)
STATEMENT_FIELDS = STATEMENT_FLOW_FIELDS + STATEMENT_BALANCE_FIELDS


@dataclass(frozen=True)
class FinancialStatementData:
    fiscal_date: date
    period: str  # "annual" or "quarterly"
    revenue: Optional[float] = None
    cost_of_revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    ebitda: Optional[float] = None
    interest_expense: Optional[float] = None
    pretax_income: Optional[float] = None
    income_tax: Optional[float] = None
    net_income: Optional[float] = None
    diluted_eps: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = None
    free_cash_flow: Optional[float] = None
    dividends_paid: Optional[float] = None
    share_repurchases: Optional[float] = None
    stock_based_compensation: Optional[float] = None
    depreciation: Optional[float] = None
    diluted_shares: Optional[float] = None
    shares_outstanding: Optional[float] = None
    total_assets: Optional[float] = None
    current_assets: Optional[float] = None
    cash_and_investments: Optional[float] = None
    receivables: Optional[float] = None
    inventory: Optional[float] = None
    total_liabilities: Optional[float] = None
    current_liabilities: Optional[float] = None
    total_debt: Optional[float] = None
    long_term_debt: Optional[float] = None
    shareholders_equity: Optional[float] = None
    retained_earnings: Optional[float] = None


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

    def get_financial_statements(self, ticker: str) -> list[FinancialStatementData]:
        """Annual and quarterly statements, oldest first. Providers without statements return []."""
        return []

    def close(self) -> None:
        """Release connections held by the provider."""
