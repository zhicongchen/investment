from datetime import date

import pytest

from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData
from app.providers.factory import CompositeMarketDataProvider, market_data_provider
from app.providers.moomoo_provider import MoomooProvider, period_days
from app.providers.yfinance_provider import YFinanceProvider
from shared.moomoo import DailyBar, MoomooError


class FakeClient:
    def __init__(self):
        self.closed = False

    def stock_basic_info(self, tickers):
        return {"TST": {"name": "Test Corp", "exchange": "NASDAQ"}}

    def daily_bars(self, ticker, start, adjusted=True):
        factor = 0.5 if adjusted else 1.0
        return [
            DailyBar(date=date(2026, 9, 22), open=10, high=11, low=9, close=10 * factor, volume=1000),
            DailyBar(date=date(2026, 9, 23), open=10, high=12, low=10, close=12, volume=2000),
        ]

    def stock_snapshots(self, tickers):
        return {"TST": {"pe_ttm": 21.5, "dividend_yield_ttm": 0.0}}

    def close(self):
        self.closed = True


def test_moomoo_provider_maps_prices_profile_and_fundamentals():
    client = FakeClient()
    provider = MoomooProvider(client)

    profile = provider.get_company_profile("tst")
    bars = provider.get_price_history("TST", "1y")
    fundamentals = provider.get_fundamentals("TST")
    provider.close()

    assert profile == CompanyProfile(ticker="TST", name="Test Corp", sector="", exchange="NASDAQ")
    assert bars[0] == PriceBarData(date=date(2026, 9, 22), open=10, high=11, low=9, close=10, volume=1000, adjusted_close=5)
    assert bars[1].close == bars[1].adjusted_close == 12
    assert fundamentals.pe == 21.5
    assert fundamentals.dividend_yield == 0.0
    assert client.closed


def test_moomoo_provider_profile_survives_lookup_failure():
    class FailingClient(FakeClient):
        def stock_basic_info(self, tickers):
            raise MoomooError("no permission")

    assert MoomooProvider(FailingClient()).get_company_profile("TST").name == "TST"


def test_period_days():
    assert period_days("5y") == 1830
    assert period_days("6mo") == 186
    assert period_days("30d") == 30
    with pytest.raises(ValueError):
        period_days("forever")


def test_factory_uses_moomoo_when_opend_is_up(monkeypatch):
    monkeypatch.setattr("app.providers.factory.MARKET_DATA_PROVIDER", "moomoo")
    monkeypatch.setattr("app.providers.factory.FMP_API_KEY", "")
    monkeypatch.setattr("app.providers.factory.is_reachable", lambda *args, **kwargs: True)

    provider = market_data_provider()

    assert isinstance(provider, CompositeMarketDataProvider)
    assert isinstance(provider.prices, MoomooProvider)
    assert isinstance(provider.fundamentals, YFinanceProvider)


def test_factory_falls_back_to_yfinance_when_opend_is_down(monkeypatch):
    monkeypatch.setattr("app.providers.factory.MARKET_DATA_PROVIDER", "moomoo")
    monkeypatch.setattr("app.providers.factory.FMP_API_KEY", "")
    monkeypatch.setattr("app.providers.factory.is_reachable", lambda *args, **kwargs: False)

    assert isinstance(market_data_provider(), YFinanceProvider)


def test_composite_fills_missing_profile_fields_and_closes_both():
    closed = []

    class Prices(MarketDataProvider):
        def get_company_profile(self, ticker):
            return CompanyProfile(ticker=ticker, name="Test Corp", exchange="NASDAQ")

        def get_price_history(self, ticker, period="5y"):
            return []

        def get_fundamentals(self, ticker):
            return FundamentalsData(date=date(2026, 9, 23))

        def close(self):
            closed.append("prices")

    class Fundamentals(Prices):
        def get_company_profile(self, ticker):
            return CompanyProfile(ticker=ticker, name="Other", sector="Technology", exchange="NMS")

        def close(self):
            closed.append("fundamentals")

    provider = CompositeMarketDataProvider(prices=Prices(), fundamentals=Fundamentals())
    profile = provider.get_company_profile("TST")
    provider.close()

    assert profile == CompanyProfile(ticker="TST", name="Test Corp", sector="Technology", exchange="NASDAQ")
    assert closed == ["prices", "fundamentals"]
