from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Company, FinancialStatement, PriceBar, Signal
from app.providers.base import CompanyProfile, FinancialStatementData, FundamentalsData, MarketDataProvider, PriceBarData
from app.services.refresh import ensure_company, refresh_all, refresh_ticker


class FakeProvider(MarketDataProvider):
    def get_company_profile(self, ticker: str) -> CompanyProfile:
        return CompanyProfile(ticker=ticker, name=f"{ticker} Inc.", sector="Technology", exchange="NMS")

    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        first = date(2024, 1, 1)
        return [
            PriceBarData(
                date=first + timedelta(days=i),
                open=100 + i,
                high=101 + i,
                low=99 + i,
                close=100 + i,
                adjusted_close=100 + i,
                volume=1000,
            )
            for i in range(220)
        ]

    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        return FundamentalsData(
            date=date(2024, 12, 31),
            pe=18,
            forward_pe=14,
            eps_growth=0.18,
            revenue_growth=0.11,
            debt_to_equity=35,
            free_cash_flow=100_000_000,
            dividend_yield=0.01,
        )


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_refresh_ticker_upserts_prices_and_signal():
    db = session()

    refresh_ticker(db, "tst", FakeProvider())
    db.commit()

    company = db.get(Company, "TST")
    bars = db.scalars(select(PriceBar).where(PriceBar.ticker == "TST")).all()
    signal = db.scalars(select(Signal).where(Signal.ticker == "TST")).first()

    assert company is not None
    assert company.name == "TST Inc."
    assert len(bars) == 220
    assert signal is not None
    assert signal.action in {"BUY", "WATCH", "HOLD", "TRIM", "SELL_REVIEW"}


def test_refresh_all_reports_failures_without_stopping():
    db = session()
    ensure_company(db, "TST")
    db.commit()

    refreshed, failed = refresh_all(db, FakeProvider())

    assert refreshed == ["TST"]
    assert failed == {}



class StatementsProvider(FakeProvider):
    def get_financial_statements(self, ticker: str) -> list[FinancialStatementData]:
        return [
            FinancialStatementData(fiscal_date=date(2024, 12, 31), period="annual", revenue=100.0, net_income=20.0, total_assets=300.0),
            FinancialStatementData(fiscal_date=date(2025, 3, 31), period="quarterly", revenue=27.0, net_income=6.0, total_assets=310.0),
        ]


class BrokenStatementsProvider(FakeProvider):
    def get_financial_statements(self, ticker: str) -> list[FinancialStatementData]:
        raise RuntimeError("statements endpoint down")


def test_refresh_ticker_upserts_financial_statements():
    db = session()

    refresh_ticker(db, "TST", StatementsProvider())
    refresh_ticker(db, "TST", StatementsProvider())
    db.commit()

    statements = db.scalars(select(FinancialStatement).where(FinancialStatement.ticker == "TST")).all()
    assert len(statements) == 2
    annual = next(row for row in statements if row.period == "annual")
    assert annual.revenue == 100.0
    assert annual.total_assets == 300.0


def test_statement_failure_does_not_fail_price_refresh():
    db = session()

    refresh_ticker(db, "TST", BrokenStatementsProvider())
    db.commit()

    assert len(db.scalars(select(PriceBar).where(PriceBar.ticker == "TST")).all()) == 220
    assert db.scalars(select(FinancialStatement)).all() == []
