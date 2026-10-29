from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import EQUITY_RISK_PREMIUM, REQUIRED_MARGIN_OF_SAFETY, RISK_FREE_RATE, TERMINAL_GROWTH_RATE
from app.models import Company, FinancialStatement, FundamentalsSnapshot, Holding, PriceBar
from app.services.analytics import BENCHMARK_TICKER, prices_for
from app.services.financials import ValuationAssumptions, analyze_company
from app.services.refresh import _portfolio_value


def valuation_assumptions() -> ValuationAssumptions:
    return ValuationAssumptions(
        risk_free_rate=RISK_FREE_RATE,
        equity_risk_premium=EQUITY_RISK_PREMIUM,
        terminal_growth=TERMINAL_GROWTH_RATE,
        required_margin_of_safety=REQUIRED_MARGIN_OF_SAFETY,
    )


def statements_for(db: Session, ticker: str) -> list[FinancialStatement]:
    return db.scalars(
        select(FinancialStatement)
        .where(FinancialStatement.ticker == ticker.upper())
        .order_by(FinancialStatement.fiscal_date.asc())
    ).all()


def company_financial_analysis(
    db: Session,
    ticker: str,
    prices: Optional[list[PriceBar]] = None,
    benchmark_prices: Optional[list[PriceBar]] = None,
    portfolio_value: Optional[float] = None,
) -> dict[str, Any]:
    """Run the financial analysis on everything stored for one ticker.

    Callers that analyze many tickers pass the benchmark prices and portfolio value once.
    """
    symbol = ticker.upper()
    company = db.get(Company, symbol)
    fundamentals = db.scalars(
        select(FundamentalsSnapshot)
        .where(FundamentalsSnapshot.ticker == symbol)
        .order_by(FundamentalsSnapshot.date.desc())
        .limit(1)
    ).first()
    return analyze_company(
        ticker=symbol,
        sector=company.sector if company else "",
        statements=statements_for(db, symbol),
        prices=prices if prices is not None else prices_for(db, symbol),
        benchmark_prices=benchmark_prices if benchmark_prices is not None else prices_for(db, BENCHMARK_TICKER),
        fundamentals=fundamentals,
        holding=db.get(Holding, symbol),
        portfolio_value=portfolio_value if portfolio_value is not None else _portfolio_value(db),
        assumptions=valuation_assumptions(),
    )
