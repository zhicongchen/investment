from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)


class CompanyRead(BaseModel):
    ticker: str
    name: str = ""
    sector: str = ""
    exchange: str = ""
    active: bool = True

    model_config = {"from_attributes": True}


class HoldingUpsert(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    shares: float = Field(ge=0)
    average_cost: float = Field(ge=0)
    target_allocation: float = Field(ge=0, le=1)
    notes: str = ""


class HoldingRead(HoldingUpsert):
    market_value: float = 0
    unrealized_gain_loss: float = 0
    current_allocation: float = 0


class PricePoint(BaseModel):
    date: date
    close: Optional[float]
    adjusted_close: Optional[float]
    volume: Optional[float]
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    bb_mid: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    drawdown: Optional[float] = None


class PriceMetrics(BaseModel):
    latest_price: Optional[float] = None
    latest_price_date: Optional[date] = None
    ytd_return: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    annualized_volatility: Optional[float] = None
    max_drawdown: Optional[float] = None
    total_return: Optional[float] = None
    benchmark_return: Optional[float] = None
    relative_return: Optional[float] = None


class FundamentalsRead(BaseModel):
    date: date
    pe: Optional[float]
    forward_pe: Optional[float]
    eps_growth: Optional[float]
    revenue_growth: Optional[float]
    debt_to_equity: Optional[float]
    free_cash_flow: Optional[float]
    dividend_yield: Optional[float]

    model_config = {"from_attributes": True}


class FundamentalsUpsert(BaseModel):
    pe: Optional[float] = None
    forward_pe: Optional[float] = None
    eps_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    debt_to_equity: Optional[float] = None
    free_cash_flow: Optional[float] = None
    dividend_yield: Optional[float] = None


class SignalRead(BaseModel):
    date: date
    action: str
    score: float
    confidence: str
    reasons: list[str]
    risk_flags: list[str]


class VerdictSummary(BaseModel):
    action: str
    conviction: str
    composite_score: Optional[float]
    margin_of_safety: Optional[float] = None


class WatchlistRow(BaseModel):
    ticker: str
    name: str
    sector: str
    exchange: str
    latest_price: Optional[float]
    latest_price_date: Optional[date]
    signal: Optional[SignalRead]
    stale: bool
    metrics: Optional[PriceMetrics] = None
    verdict: Optional[VerdictSummary] = None


class CompanyDetail(BaseModel):
    company: CompanyRead
    prices: list[PricePoint]
    metrics: Optional[PriceMetrics]
    fundamentals: Optional[FundamentalsRead]
    signals: list[SignalRead]
    holding: Optional[HoldingRead]


class RefreshResult(BaseModel):
    refreshed: list[str]
    failed: dict[str, str]


class MarketComparison(BaseModel):
    benchmark: str
    tickers: list[str]
    cumulative_returns: list[dict]
    correlation: list[dict]


class PortfolioAllocation(BaseModel):
    ticker: str
    current_allocation: float
    target_allocation: float
    drift: float


class PortfolioAnalytics(BaseModel):
    benchmark: str
    total_value: float
    total_return: Optional[float]
    annualized_volatility: Optional[float]
    sharpe_ratio: Optional[float]
    series: list[dict]
    allocation: list[PortfolioAllocation]


class DemoImportResult(BaseModel):
    imported: list[str]


class OptionsScanRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=30)
    source: Literal["auto", "moomoo", "yahoo"] = "auto"
    only_dipping: bool = False
    include_positions: bool = False
    fundamentals: Literal["dashboard", "off"] = "dashboard"


class OllamaStatus(BaseModel):
    base_url: str
    model: str
    reachable: bool
    error: Optional[str] = None


class OllamaAnalysisRead(BaseModel):
    id: int
    subject_type: str
    ticker: Optional[str]
    model: str
    prompt_version: str
    analysis_type: str
    data_snapshot_date: Optional[date]
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AnalysisMetric(BaseModel):
    key: str
    label: str
    value: Optional[float]
    format: str
    score: Optional[float]
    weight: float
    note: str = ""


class AnalysisPillar(BaseModel):
    key: str
    label: str
    weight: float
    score: Optional[float]
    metrics: list[AnalysisMetric]


class AnnualFinancials(BaseModel):
    fiscal_date: date
    revenue: Optional[float]
    revenue_growth: Optional[float]
    gross_margin: Optional[float]
    operating_margin: Optional[float]
    net_margin: Optional[float]
    free_cash_flow: Optional[float]
    fcf_margin: Optional[float]
    roe: Optional[float]
    roic: Optional[float]
    debt_to_equity: Optional[float]
    current_ratio: Optional[float]
    diluted_eps: Optional[float]
    diluted_shares: Optional[float]


class PiotroskiCheck(BaseModel):
    name: str
    passed: bool


class PiotroskiScore(BaseModel):
    score: int
    max: int
    normalized: float
    fiscal_date: date
    checks: list[PiotroskiCheck]


class ValuationSummary(BaseModel):
    method: str
    applicable: bool
    note: str = ""
    price: Optional[float]
    bear_value: Optional[float]
    base_value: Optional[float]
    bull_value: Optional[float]
    margin_of_safety: Optional[float]
    upside: Optional[float]
    entry_price: Optional[float]
    trim_price: Optional[float]
    discount_rate: Optional[float]
    cost_of_equity: Optional[float]
    beta: Optional[float]
    stage_one_growth: Optional[float]
    growth_source: str = ""
    terminal_growth: Optional[float]
    terminal_value_share: Optional[float]
    implied_growth: Optional[float]
    base_cash_flow: Optional[float]
    cash_flow_basis: str = ""
    required_margin_of_safety: float


class AnalysisFlag(BaseModel):
    severity: str
    message: str


class PositionContext(BaseModel):
    shares: float
    average_cost: float
    market_value: Optional[float]
    unrealized_return: Optional[float]
    allocation: Optional[float]
    target_allocation: Optional[float]
    overweight: bool
    underweight: bool


class InvestmentRecommendation(BaseModel):
    action: str
    conviction: str
    composite_score: Optional[float]
    summary: str
    rationale: list[str]
    strengths: list[str]
    concerns: list[str]
    triggers: list[str]
    timing_note: Optional[str]


class FinancialAnalysisRead(BaseModel):
    ticker: str
    sector: str
    as_of: Optional[date]
    statement_date: Optional[date]
    statement_basis: str
    is_financial_sector: bool
    data_coverage: float
    price: Optional[float]
    market_cap: Optional[float]
    enterprise_value: Optional[float]
    pillars: list[AnalysisPillar]
    composite_score: Optional[float]
    annual: list[AnnualFinancials]
    piotroski: Optional[PiotroskiScore]
    altman_z: Optional[float]
    economic_spread: Optional[float]
    valuation: ValuationSummary
    flags: list[AnalysisFlag]
    position: Optional[PositionContext]
    recommendation: InvestmentRecommendation
    warnings: list[str]
