from __future__ import annotations

from app.config import FMP_API_KEY
from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData
from app.providers.fmp_provider import FMPProvider
from app.providers.yfinance_provider import YFinanceProvider


class CompositeMarketDataProvider(MarketDataProvider):
    def __init__(self, prices: MarketDataProvider, fundamentals: MarketDataProvider) -> None:
        self.prices = prices
        self.fundamentals = fundamentals

    def get_company_profile(self, ticker: str) -> CompanyProfile:
        return self.prices.get_company_profile(ticker)

    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        return self.prices.get_price_history(ticker, period)

    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        primary = self.fundamentals.get_fundamentals(ticker)
        fallback = self.prices.get_fundamentals(ticker)
        return FundamentalsData(
            date=primary.date,
            pe=primary.pe if primary.pe is not None else fallback.pe,
            forward_pe=primary.forward_pe if primary.forward_pe is not None else fallback.forward_pe,
            eps_growth=primary.eps_growth if primary.eps_growth is not None else fallback.eps_growth,
            revenue_growth=primary.revenue_growth if primary.revenue_growth is not None else fallback.revenue_growth,
            debt_to_equity=primary.debt_to_equity if primary.debt_to_equity is not None else fallback.debt_to_equity,
            free_cash_flow=primary.free_cash_flow if primary.free_cash_flow is not None else fallback.free_cash_flow,
            dividend_yield=primary.dividend_yield if primary.dividend_yield is not None else fallback.dividend_yield,
        )


def market_data_provider() -> MarketDataProvider:
    yfinance = YFinanceProvider()
    if FMP_API_KEY:
        return CompositeMarketDataProvider(prices=yfinance, fundamentals=FMPProvider(FMP_API_KEY))
    return yfinance
