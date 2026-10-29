from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Company(Base):
    __tablename__ = "companies"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    sector: Mapped[str] = mapped_column(String(128), default="")
    exchange: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    prices: Mapped[list["PriceBar"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    fundamentals: Mapped[list["FundamentalsSnapshot"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    signals: Mapped[list["Signal"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    statements: Mapped[list["FinancialStatement"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_price_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("companies.ticker"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Optional[float]] = mapped_column(Float)
    high: Mapped[Optional[float]] = mapped_column(Float)
    low: Mapped[Optional[float]] = mapped_column(Float)
    close: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[float]] = mapped_column(Float)
    adjusted_close: Mapped[Optional[float]] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="prices")


class FundamentalsSnapshot(Base):
    __tablename__ = "fundamentals_snapshots"
    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_fundamentals_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("companies.ticker"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    pe: Mapped[Optional[float]] = mapped_column(Float)
    forward_pe: Mapped[Optional[float]] = mapped_column(Float)
    eps_growth: Mapped[Optional[float]] = mapped_column(Float)
    revenue_growth: Mapped[Optional[float]] = mapped_column(Float)
    debt_to_equity: Mapped[Optional[float]] = mapped_column(Float)
    free_cash_flow: Mapped[Optional[float]] = mapped_column(Float)
    dividend_yield: Mapped[Optional[float]] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="fundamentals")


class FinancialStatement(Base):
    """One reported period (annual or quarterly) of income statement, balance sheet and cash flow lines.

    Field conventions match app.providers.base.FinancialStatementData.
    """

    __tablename__ = "financial_statements"
    __table_args__ = (UniqueConstraint("ticker", "period", "fiscal_date", name="uq_statement_ticker_period_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("companies.ticker"), index=True)
    period: Mapped[str] = mapped_column(String(16))
    fiscal_date: Mapped[date] = mapped_column(Date, index=True)
    revenue: Mapped[Optional[float]] = mapped_column(Float)
    cost_of_revenue: Mapped[Optional[float]] = mapped_column(Float)
    gross_profit: Mapped[Optional[float]] = mapped_column(Float)
    operating_income: Mapped[Optional[float]] = mapped_column(Float)
    ebitda: Mapped[Optional[float]] = mapped_column(Float)
    interest_expense: Mapped[Optional[float]] = mapped_column(Float)
    pretax_income: Mapped[Optional[float]] = mapped_column(Float)
    income_tax: Mapped[Optional[float]] = mapped_column(Float)
    net_income: Mapped[Optional[float]] = mapped_column(Float)
    diluted_eps: Mapped[Optional[float]] = mapped_column(Float)
    operating_cash_flow: Mapped[Optional[float]] = mapped_column(Float)
    capital_expenditure: Mapped[Optional[float]] = mapped_column(Float)
    free_cash_flow: Mapped[Optional[float]] = mapped_column(Float)
    dividends_paid: Mapped[Optional[float]] = mapped_column(Float)
    share_repurchases: Mapped[Optional[float]] = mapped_column(Float)
    stock_based_compensation: Mapped[Optional[float]] = mapped_column(Float)
    depreciation: Mapped[Optional[float]] = mapped_column(Float)
    diluted_shares: Mapped[Optional[float]] = mapped_column(Float)
    shares_outstanding: Mapped[Optional[float]] = mapped_column(Float)
    total_assets: Mapped[Optional[float]] = mapped_column(Float)
    current_assets: Mapped[Optional[float]] = mapped_column(Float)
    cash_and_investments: Mapped[Optional[float]] = mapped_column(Float)
    receivables: Mapped[Optional[float]] = mapped_column(Float)
    inventory: Mapped[Optional[float]] = mapped_column(Float)
    total_liabilities: Mapped[Optional[float]] = mapped_column(Float)
    current_liabilities: Mapped[Optional[float]] = mapped_column(Float)
    total_debt: Mapped[Optional[float]] = mapped_column(Float)
    long_term_debt: Mapped[Optional[float]] = mapped_column(Float)
    shareholders_equity: Mapped[Optional[float]] = mapped_column(Float)
    retained_earnings: Mapped[Optional[float]] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="statements")


class Holding(Base):
    __tablename__ = "holdings"

    ticker: Mapped[str] = mapped_column(ForeignKey("companies.ticker"), primary_key=True)
    shares: Mapped[float] = mapped_column(Float, default=0)
    average_cost: Mapped[float] = mapped_column(Float, default=0)
    target_allocation: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_signal_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("companies.ticker"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    action: Mapped[str] = mapped_column(String(24))
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(16))
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    risk_flags: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="signals")


class OllamaAnalysis(Base):
    __tablename__ = "ollama_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(24), index=True)
    ticker: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    analysis_type: Mapped[str] = mapped_column(String(64), default="deep_analysis")
    data_snapshot_date: Mapped[Optional[date]] = mapped_column(Date)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
