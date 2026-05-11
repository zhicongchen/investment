from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Company, Holding, PriceBar
from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData
from app.services.analytics import (
    BENCHMARK_TICKER,
    annualized_volatility,
    correlation_matrix,
    daily_returns,
    enrich_price_points,
    import_demo_csvs,
    max_drawdown,
    portfolio_analytics,
)
from app.services.refresh import ensure_company, refresh_all


def bars(ticker: str = "TST", days: int = 260, start: float = 100, step: float = 1) -> list[PriceBar]:
    first = date(2024, 1, 1)
    return [
        PriceBar(
            ticker=ticker,
            date=first + timedelta(days=index),
            open=start + index * step,
            high=start + index * step + 1,
            low=start + index * step - 1,
            close=start + index * step,
            adjusted_close=start + index * step,
            volume=1000,
        )
        for index in range(days)
    ]


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


class FakeProvider(MarketDataProvider):
    def get_company_profile(self, ticker: str) -> CompanyProfile:
        return CompanyProfile(ticker=ticker, name=ticker)

    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        return [
            PriceBarData(
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adjusted_close=bar.adjusted_close,
                volume=bar.volume,
            )
            for bar in bars(ticker)
        ]

    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        return FundamentalsData(date=date(2024, 12, 31), pe=18, forward_pe=16)


def test_enrich_price_points_adds_technical_indicators():
    points = enrich_price_points(bars())
    latest = points[-1]

    assert latest["ma20"] is not None
    assert latest["ma50"] is not None
    assert latest["ma200"] is not None
    assert latest["rsi"] is not None
    assert latest["macd"] is not None
    assert latest["bb_upper"] is not None
    assert latest["drawdown"] == 0


def test_returns_volatility_drawdown_and_correlation():
    upward = bars("AAA", days=30, start=100, step=1)
    downward = bars("BBB", days=30, start=130, step=-1)

    assert len(daily_returns(upward)) == 29
    assert annualized_volatility(upward) is not None
    assert max_drawdown(downward) < 0
    matrix = correlation_matrix({"AAA": upward, "BBB": downward})

    assert matrix[0]["AAA"] == 1
    assert matrix[0]["BBB"] is not None


def test_refresh_all_fetches_hidden_benchmark():
    db = session()
    ensure_company(db, "TST")
    db.commit()

    refreshed, failed = refresh_all(db, FakeProvider())
    benchmark = db.get(Company, BENCHMARK_TICKER)

    assert refreshed == ["TST"]
    assert failed == {}
    assert benchmark is not None
    assert benchmark.active is False


def test_portfolio_analytics_uses_holdings_and_benchmark():
    db = session()
    db.add(Company(ticker="TST", name="TST", active=True))
    db.add(Company(ticker=BENCHMARK_TICKER, name=BENCHMARK_TICKER, active=False))
    for bar in bars("TST", days=20):
        db.add(bar)
    for bar in bars(BENCHMARK_TICKER, days=20, start=100, step=0.5):
        db.add(bar)
    db.add(Holding(ticker="TST", shares=2, average_cost=100, target_allocation=1))
    db.commit()

    analytics = portfolio_analytics(db)

    assert analytics["total_value"] > 0
    assert analytics["total_return"] is not None
    assert analytics["series"]
    assert analytics["allocation"][0]["ticker"] == "TST"
