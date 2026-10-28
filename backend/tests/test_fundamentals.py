from datetime import date, timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import router
from app.db import Base, get_db
from app.models import Company, FinancialStatement, FundamentalsSnapshot, Holding, PriceBar, Signal
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


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_fmp_provider_maps_financial_statements(monkeypatch):
    annual = {
        "income-statement": [{"date": "2024-12-31", "revenue": 1000, "operatingIncome": 300, "interestExpense": -12, "netIncome": 220, "epsDiluted": 2.2}],
        "balance-sheet-statement": [{"date": "2024-12-31", "totalAssets": 2500, "totalDebt": 400, "totalStockholdersEquity": 1200}],
        "cash-flow-statement": [{"date": "2024-12-31", "operatingCashFlow": 350, "capitalExpenditure": -50, "freeCashFlow": 300, "commonStockRepurchased": -80}],
    }

    def fake_urlopen(request, timeout):
        url = request.full_url
        endpoint = url.split("/stable/")[1].split("?")[0]
        if "period=quarter" in url:
            raise OSError("402 Payment Required")
        return FakeResponse(annual[endpoint])

    monkeypatch.setattr("app.providers.fmp_provider.urlopen", fake_urlopen)

    statements = FMPProvider("demo").get_financial_statements("tst")

    assert len(statements) == 1
    statement = statements[0]
    assert statement.period == "annual"
    assert statement.fiscal_date == date(2024, 12, 31)
    assert statement.revenue == 1000
    assert statement.interest_expense == 12
    assert statement.capital_expenditure == 50
    assert statement.share_repurchases == 80
    assert statement.total_debt == 400


def test_yfinance_provider_maps_statement_frames(monkeypatch):
    import pandas as pd

    end = pd.Timestamp("2024-09-30")
    older = pd.Timestamp("2023-09-30")
    income = pd.DataFrame({end: {"Total Revenue": 1000.0, "Operating Income": 300.0, "Net Income": 220.0}, older: {"Total Revenue": None}})
    balance = pd.DataFrame({end: {"Total Assets": 2500.0, "Stockholders Equity": 1200.0, "Total Debt": 400.0}})
    cash_flow = pd.DataFrame({end: {"Operating Cash Flow": 350.0, "Capital Expenditure": -50.0, "Free Cash Flow": 300.0}})

    class FakeTicker:
        income_stmt = income
        balance_sheet = balance
        cashflow = cash_flow
        quarterly_income_stmt = pd.DataFrame()
        quarterly_balance_sheet = pd.DataFrame()
        quarterly_cashflow = pd.DataFrame()

        def __init__(self, symbol):
            self.symbol = symbol

    monkeypatch.setattr("app.providers.yfinance_provider.yf.Ticker", FakeTicker)

    statements = YFinanceProvider().get_financial_statements("tst")

    assert len(statements) == 1
    statement = statements[0]
    assert statement.fiscal_date == date(2024, 9, 30)
    assert statement.revenue == 1000.0
    assert statement.capital_expenditure == 50.0
    assert statement.shareholders_equity == 1200.0


def test_financial_analysis_endpoint_and_watchlist_verdict():
    db = session()
    add_prices(db)
    add_prices(db, "SPY")
    for index, year in enumerate(range(2021, 2025)):
        revenue = 1_000e6 * 1.1**index
        db.add(
            FinancialStatement(
                ticker="TST",
                period="annual",
                fiscal_date=date(year, 12, 31),
                revenue=revenue,
                gross_profit=revenue * 0.6,
                operating_income=revenue * 0.25,
                ebitda=revenue * 0.3,
                interest_expense=5e6,
                pretax_income=revenue * 0.24,
                income_tax=revenue * 0.05,
                net_income=revenue * 0.19,
                diluted_eps=revenue * 0.19 / 50e6,
                operating_cash_flow=revenue * 0.27,
                capital_expenditure=revenue * 0.04,
                diluted_shares=50e6,
                shares_outstanding=50e6,
                total_assets=revenue * 1.4,
                current_assets=revenue * 0.6,
                cash_and_investments=200e6,
                total_liabilities=revenue * 0.6,
                current_liabilities=revenue * 0.3,
                total_debt=150e6,
                long_term_debt=140e6,
                shareholders_equity=revenue * 0.8,
                retained_earnings=revenue * 0.5,
            )
        )
    db.add(Holding(ticker="TST", shares=10, average_cost=100, target_allocation=1.0))
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    response = client.get("/api/companies/TST/financial-analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["statement_basis"] == "FY"
    assert body["valuation"]["applicable"] is True
    assert body["piotroski"]["max"] >= 6
    assert body["position"]["shares"] == 10
    assert body["recommendation"]["action"] in {"ADD", "HOLD", "TRIM", "SELL_REVIEW"}
    assert {pillar["key"] for pillar in body["pillars"]} == {"quality", "strength", "growth", "valuation", "momentum"}

    watchlist = client.get("/api/watchlist").json()
    row = next(item for item in watchlist if item["ticker"] == "TST")
    assert row["verdict"]["action"] == body["recommendation"]["action"]

    assert client.get("/api/companies/NOPE/financial-analysis").status_code == 404
