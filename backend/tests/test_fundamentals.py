from datetime import date, timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import router
from app.db import Base, get_db
from app.models import Company, FundamentalsSnapshot, PriceBar, Signal
from app.providers.factory import CompositeMarketDataProvider, market_data_provider
from app.providers.fmp_provider import FMPProvider
from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData
from app.providers.yfinance_provider import YFinanceProvider


def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def add_prices(db, ticker: str = "TST") -> None:
    first = date.today() - timedelta(days=240)
    db.add(Company(ticker=ticker, name=ticker, active=True))
    for index in range(240):
        db.add(
            PriceBar(
                ticker=ticker,
                date=first + timedelta(days=index),
                open=100 + index,
                high=101 + index,
                low=99 + index,
                close=100 + index,
                adjusted_close=100 + index,
                volume=1_000_000,
            )
        )
    db.commit()


def test_manual_fundamentals_upsert_recomputes_signal():
    db = session()
    add_prices(db)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    response = client.put(
        "/api/companies/TST/fundamentals",
        json={
            "pe": 18,
            "forward_pe": 14,
            "eps_growth": 0.2,
            "revenue_growth": 0.12,
            "debt_to_equity": 35,
            "free_cash_flow": 1000000,
            "dividend_yield": 0.01,
        },
    )

    assert response.status_code == 200
    snapshot = db.scalars(select(FundamentalsSnapshot).where(FundamentalsSnapshot.ticker == "TST")).one()
    signal = db.scalars(select(Signal).where(Signal.ticker == "TST")).one()
    assert snapshot.forward_pe == 14
    assert signal.confidence == "high"


def test_fmp_provider_maps_stable_endpoint_payloads(monkeypatch):
    payloads = {
        "profile": [{"forwardPE": 21.5, "companyName": "Test Inc.", "sector": "Technology"}],
        "ratios-ttm": [{"priceToEarningsRatioTTM": 20.0, "debtEquityRatioTTM": 0.45, "dividendYieldTTM": 1.2}],
        "key-metrics-ttm": [{"freeCashFlowTTM": 123456789}],
        "financial-growth": [{"growthEPS": 15.0, "growthRevenue": 8.5}],
        "cash-flow-statement": [{"freeCashFlow": 987654321}],
        "balance-sheet-statement": [{"totalDebt": 45, "totalStockholdersEquity": 100}],
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        url = request.full_url
        endpoint = url.split("/stable/")[1].split("?")[0]
        return FakeResponse(payloads[endpoint])

    monkeypatch.setattr("app.providers.fmp_provider.urlopen", fake_urlopen)

    fundamentals = FMPProvider("demo").get_fundamentals("tst")

    assert fundamentals.pe == 20.0
    assert fundamentals.forward_pe == 21.5
    assert fundamentals.eps_growth == 0.15
    assert fundamentals.revenue_growth == 0.085
    assert fundamentals.debt_to_equity == 0.45
    assert fundamentals.free_cash_flow == 987654321
    assert fundamentals.dividend_yield == 0.012


def test_market_data_provider_uses_yfinance_without_fmp_key(monkeypatch):
    monkeypatch.setattr("app.providers.factory.MARKET_DATA_PROVIDER", "yfinance")
    monkeypatch.setattr("app.providers.factory.FMP_API_KEY", "")

    provider = market_data_provider()

    assert isinstance(provider, YFinanceProvider)


def test_market_data_provider_composes_fmp_with_key(monkeypatch):
    monkeypatch.setattr("app.providers.factory.MARKET_DATA_PROVIDER", "yfinance")
    monkeypatch.setattr("app.providers.factory.FMP_API_KEY", "demo")

    provider = market_data_provider()

    assert isinstance(provider, CompositeMarketDataProvider)
    assert isinstance(provider.prices, YFinanceProvider)
    assert isinstance(provider.fundamentals, FMPProvider)


def test_composite_provider_fills_missing_fundamentals_from_price_provider():
    class PrimaryProvider(MarketDataProvider):
        def get_company_profile(self, ticker: str) -> CompanyProfile:
            return CompanyProfile(ticker=ticker)

        def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
            return []

        def get_fundamentals(self, ticker: str) -> FundamentalsData:
            return FundamentalsData(date=date(2024, 12, 31), pe=20, forward_pe=None)

    class FallbackProvider(PrimaryProvider):
        def get_fundamentals(self, ticker: str) -> FundamentalsData:
            return FundamentalsData(date=date(2024, 12, 30), pe=99, forward_pe=18)

    provider = CompositeMarketDataProvider(prices=FallbackProvider(), fundamentals=PrimaryProvider())

    fundamentals = provider.get_fundamentals("TST")

    assert fundamentals.pe == 20
    assert fundamentals.forward_pe == 18
