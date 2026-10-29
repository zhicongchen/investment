from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, FinancialStatement, FundamentalsSnapshot, Holding, PriceBar, Signal
from app.providers.base import STATEMENT_FIELDS, MarketDataProvider
from app.services.analytics import BENCHMARK_TICKER
from app.services.scoring import compute_signal, encode_list

logger = logging.getLogger(__name__)


def _ticker(value: str) -> str:
    return value.upper().strip()


def _portfolio_value(db: Session) -> float:
    total = 0.0
    holdings = db.scalars(select(Holding)).all()
    for holding in holdings:
        latest = db.scalars(
            select(PriceBar)
            .where(PriceBar.ticker == holding.ticker)
            .order_by(PriceBar.date.desc())
            .limit(1)
        ).first()
        close = latest.adjusted_close if latest and latest.adjusted_close is not None else latest.close if latest else None
        if close is not None:
            total += holding.shares * close
    return total


def ensure_company(db: Session, ticker: str, provider: Optional[MarketDataProvider] = None) -> Company:
    symbol = _ticker(ticker)
    company = db.get(Company, symbol)
    if company:
        company.active = True
        return company

    if provider:
        profile = provider.get_company_profile(symbol)
        company = Company(
            ticker=symbol,
            name=profile.name,
            sector=profile.sector,
            exchange=profile.exchange,
            active=True,
        )
    else:
        company = Company(ticker=symbol, name=symbol, active=True)
    db.add(company)
    db.flush()
    return company


def refresh_ticker(db: Session, ticker: str, provider: MarketDataProvider) -> None:
    company = ensure_company(db, ticker, provider)
    profile = provider.get_company_profile(company.ticker)
    company.name = profile.name or company.name
    company.sector = profile.sector or company.sector
    company.exchange = profile.exchange or company.exchange

    for bar in provider.get_price_history(company.ticker):
        existing = db.scalars(
            select(PriceBar).where(PriceBar.ticker == company.ticker, PriceBar.date == bar.date)
        ).first()
        if existing is None:
            existing = PriceBar(ticker=company.ticker, date=bar.date)
            db.add(existing)
        existing.open = bar.open
        existing.high = bar.high
        existing.low = bar.low
        existing.close = bar.close
        existing.volume = bar.volume
        existing.adjusted_close = bar.adjusted_close

    fundamentals = provider.get_fundamentals(company.ticker)
    existing_fundamentals = db.scalars(
        select(FundamentalsSnapshot).where(
            FundamentalsSnapshot.ticker == company.ticker,
            FundamentalsSnapshot.date == fundamentals.date,
        )
    ).first()
    if existing_fundamentals is None:
        existing_fundamentals = FundamentalsSnapshot(ticker=company.ticker, date=fundamentals.date)
        db.add(existing_fundamentals)
    existing_fundamentals.pe = fundamentals.pe
    existing_fundamentals.forward_pe = fundamentals.forward_pe
    existing_fundamentals.eps_growth = fundamentals.eps_growth
    existing_fundamentals.revenue_growth = fundamentals.revenue_growth
    existing_fundamentals.debt_to_equity = fundamentals.debt_to_equity
    existing_fundamentals.free_cash_flow = fundamentals.free_cash_flow
    existing_fundamentals.dividend_yield = fundamentals.dividend_yield

    if company.ticker != BENCHMARK_TICKER:
        refresh_statements(db, company.ticker, provider)

    db.flush()
    recompute_signal(db, company.ticker)


def refresh_statements(db: Session, ticker: str, provider: MarketDataProvider) -> int:
    """Upsert reported financial statements. A statements failure never fails the price refresh."""
    try:
        statements = provider.get_financial_statements(ticker)
    except Exception as exc:
        logger.warning("Financial statements for %s unavailable: %s", ticker, exc)
        return 0
    for data in statements:
        existing = db.scalars(
            select(FinancialStatement).where(
                FinancialStatement.ticker == ticker,
                FinancialStatement.period == data.period,
                FinancialStatement.fiscal_date == data.fiscal_date,
            )
        ).first()
        if existing is None:
            existing = FinancialStatement(ticker=ticker, period=data.period, fiscal_date=data.fiscal_date)
            db.add(existing)
        for field in STATEMENT_FIELDS:
            setattr(existing, field, getattr(data, field))
    return len(statements)


def recompute_signal(db: Session, ticker: str) -> Signal:
    symbol = _ticker(ticker)
    prices = db.scalars(select(PriceBar).where(PriceBar.ticker == symbol).order_by(PriceBar.date.asc())).all()
    fundamentals = db.scalars(
        select(FundamentalsSnapshot)
        .where(FundamentalsSnapshot.ticker == symbol)
        .order_by(FundamentalsSnapshot.date.desc())
        .limit(1)
    ).first()
    holding = db.get(Holding, symbol)
    decision = compute_signal(symbol, prices, fundamentals, holding, _portfolio_value(db))
    signal = db.scalars(select(Signal).where(Signal.ticker == symbol, Signal.date == decision.date)).first()
    if signal is None:
        signal = Signal(ticker=symbol, date=decision.date)
        db.add(signal)
    signal.action = decision.action
    signal.score = decision.score
    signal.confidence = decision.confidence
    signal.reasons = encode_list(decision.reasons)
    signal.risk_flags = encode_list(decision.risk_flags)
    db.flush()
    return signal


def refresh_all(db: Session, provider: MarketDataProvider) -> tuple[list[str], dict[str, str]]:
    refreshed: list[str] = []
    failed: dict[str, str] = {}
    companies = db.scalars(select(Company).where(Company.active.is_(True))).all()
    if not any(company.ticker == BENCHMARK_TICKER for company in companies):
        ensure_company(db, BENCHMARK_TICKER, provider)
        benchmark = db.get(Company, BENCHMARK_TICKER)
        if benchmark:
            benchmark.active = False
        db.flush()
    tickers = [company.ticker for company in companies] + [BENCHMARK_TICKER]
    for ticker in dict.fromkeys(tickers):
        try:
            refresh_ticker(db, ticker, provider)
            if ticker == BENCHMARK_TICKER:
                benchmark = db.get(Company, BENCHMARK_TICKER)
                if benchmark:
                    benchmark.active = False
            if ticker != BENCHMARK_TICKER:
                refreshed.append(ticker)
        except Exception as exc:
            failed[ticker] = str(exc)
    db.commit()
    return refreshed, failed
