from __future__ import annotations

import logging

from app.config import FMP_API_KEY, MARKET_DATA_PROVIDER, MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT
from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData
from app.providers.fmp_provider import FMPProvider
from app.providers.moomoo_provider import MoomooProvider
from app.providers.yfinance_provider import YFinanceProvider
from shared.moomoo import MoomooClient, is_reachable

logger = logging.getLogger(__name__)


class CompositeMarketDataProvider(MarketDataProvider):
    def __init__(self, prices: MarketDataProvider, fundamentals: MarketDataProvider) -> None:
        self.prices = prices
        self.fundamentals = fundamentals

    def get_company_profile(self, ticker: str) -> CompanyProfile:
        profile = self.prices.get_company_profile(ticker)
        if profile.name and profile.name != profile.ticker and profile.sector and profile.exchange:
            return profile
        try:
            extra = self.fundamentals.get_company_profile(ticker)
        except Exception:
            return profile
        return CompanyProfile(
            ticker=profile.ticker,
            name=profile.name if profile.name and profile.name != profile.ticker else extra.name or profile.name,
            sector=profile.sector or extra.sector,
            exchange=profile.exchange or extra.exchange,
        )

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

    def close(self) -> None:
        self.prices.close()
        self.fundamentals.close()


def market_data_provider() -> MarketDataProvider:
    """Prices from Moomoo (or yfinance), fundamentals from FMP when keyed, else yfinance."""
    prices = _price_provider()
    if FMP_API_KEY:
        return CompositeMarketDataProvider(prices=prices, fundamentals=FMPProvider(FMP_API_KEY))
    if isinstance(prices, MoomooProvider):
        # Moomoo only covers PE and dividend yield; yfinance fills sector, forward PE, growth, leverage and FCF.
        return CompositeMarketDataProvider(prices=prices, fundamentals=YFinanceProvider())
    return prices


def _price_provider() -> MarketDataProvider:
    if MARKET_DATA_PROVIDER == "moomoo":
        if is_reachable(MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT, timeout=1):
            return MoomooProvider(MoomooClient(MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT))
        logger.warning("OpenD not reachable at %s:%s; using yfinance for prices", MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT)
    return YFinanceProvider()
