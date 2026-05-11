from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Company, FundamentalsSnapshot, Holding, PriceBar, Signal
from app.providers.factory import market_data_provider
from app.schemas import (
    CompanyCreate,
    CompanyDetail,
    CompanyRead,
    FundamentalsRead,
    FundamentalsUpsert,
    HoldingRead,
    HoldingUpsert,
    DemoImportResult,
    MarketComparison,
    OllamaAnalysisRead,
    OllamaStatus,
    PortfolioAnalytics,
    PricePoint,
    RefreshResult,
    SignalRead,
    WatchlistRow,
)
from app.services.analytics import (
    BENCHMARK_TICKER,
    enrich_price_points,
    import_demo_csvs,
    market_comparison,
    portfolio_analytics,
    prices_for,
    summary_metrics,
)
from app.services.refresh import ensure_company, recompute_signal, refresh_all, refresh_ticker
from app.services.scoring import decode_list
from app.services.ollama import (
    OllamaClient,
    OllamaError,
    analysis_history,
    generate_portfolio_analysis,
    generate_ticker_analysis,
)

router = APIRouter()


def _signal_read(signal: Optional[Signal]) -> Optional[SignalRead]:
    if signal is None:
        return None
    return SignalRead(
        date=signal.date,
        action=signal.action,
        score=signal.score,
        confidence=signal.confidence,
        reasons=decode_list(signal.reasons),
        risk_flags=decode_list(signal.risk_flags),
    )


def _latest_price(db: Session, ticker: str) -> Optional[PriceBar]:
    return db.scalars(select(PriceBar).where(PriceBar.ticker == ticker).order_by(PriceBar.date.desc()).limit(1)).first()


def _latest_signal(db: Session, ticker: str) -> Optional[Signal]:
    return db.scalars(select(Signal).where(Signal.ticker == ticker).order_by(Signal.date.desc()).limit(1)).first()


def _portfolio_value(db: Session) -> float:
    total = 0.0
    holdings = db.scalars(select(Holding)).all()
    for holding in holdings:
        latest = _latest_price(db, holding.ticker)
        close = latest.adjusted_close if latest and latest.adjusted_close is not None else latest.close if latest else None
        if close is not None:
            total += holding.shares * close
    return total


def _holding_read(db: Session, holding: Holding) -> HoldingRead:
    latest = _latest_price(db, holding.ticker)
    close = latest.adjusted_close if latest and latest.adjusted_close is not None else latest.close if latest else None
    market_value = holding.shares * close if close is not None else 0
    cost_basis = holding.shares * holding.average_cost
    portfolio_value = _portfolio_value(db)
    return HoldingRead(
        ticker=holding.ticker,
        shares=holding.shares,
        average_cost=holding.average_cost,
        target_allocation=holding.target_allocation,
        notes=holding.notes,
        market_value=round(market_value, 2),
        unrealized_gain_loss=round(market_value - cost_basis, 2),
        current_allocation=round(market_value / portfolio_value, 4) if portfolio_value else 0,
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/watchlist", response_model=list[WatchlistRow])
def list_watchlist(db: Session = Depends(get_db)) -> list[WatchlistRow]:
    companies = db.scalars(select(Company).where(Company.active.is_(True)).order_by(Company.ticker.asc())).all()
    rows: list[WatchlistRow] = []
    stale_before = date.today() - timedelta(days=5)
    benchmark_prices = prices_for(db, BENCHMARK_TICKER)
    for company in companies:
        latest = _latest_price(db, company.ticker)
        signal = _latest_signal(db, company.ticker)
        close = latest.adjusted_close if latest and latest.adjusted_close is not None else latest.close if latest else None
        prices = prices_for(db, company.ticker)
        rows.append(
            WatchlistRow(
                ticker=company.ticker,
                name=company.name,
                sector=company.sector,
                exchange=company.exchange,
                latest_price=close,
                latest_price_date=latest.date if latest else None,
                signal=_signal_read(signal),
                stale=latest is None or latest.date < stale_before,
                metrics=summary_metrics(prices, benchmark_prices),
            )
        )
    return rows


@router.post("/watchlist", response_model=CompanyRead)
def add_company(payload: CompanyCreate, db: Session = Depends(get_db)) -> Company:
    company = ensure_company(db, payload.ticker)
    db.commit()
    db.refresh(company)
    return company


@router.delete("/watchlist/{ticker}")
def deactivate_company(ticker: str, db: Session = Depends(get_db)) -> dict[str, str]:
    company = db.get(Company, ticker.upper())
    if not company:
        raise HTTPException(status_code=404, detail="Ticker not found")
    company.active = False
    db.commit()
    return {"status": "removed"}


@router.post("/refresh", response_model=RefreshResult)
def refresh_watchlist(db: Session = Depends(get_db)) -> RefreshResult:
    refreshed, failed = refresh_all(db, market_data_provider())
    return RefreshResult(refreshed=refreshed, failed=failed)


@router.post("/refresh/{ticker}", response_model=RefreshResult)
def refresh_single(ticker: str, db: Session = Depends(get_db)) -> RefreshResult:
    symbol = ticker.upper()
    try:
        refresh_ticker(db, symbol, market_data_provider())
        db.commit()
    except Exception as exc:
        db.rollback()
        return RefreshResult(refreshed=[], failed={symbol: str(exc)})
    return RefreshResult(refreshed=[symbol], failed={})


@router.get("/companies/{ticker}", response_model=CompanyDetail)
def company_detail(ticker: str, db: Session = Depends(get_db)) -> CompanyDetail:
    symbol = ticker.upper()
    company = db.get(Company, symbol)
    if not company:
        raise HTTPException(status_code=404, detail="Ticker not found")

    prices = prices_for(db, symbol)
    price_points = [PricePoint(**point) for point in enrich_price_points(prices)]
    benchmark_prices = prices_for(db, BENCHMARK_TICKER)

    fundamentals = db.scalars(
        select(FundamentalsSnapshot)
        .where(FundamentalsSnapshot.ticker == symbol)
        .order_by(FundamentalsSnapshot.date.desc())
        .limit(1)
    ).first()
    signals = db.scalars(select(Signal).where(Signal.ticker == symbol).order_by(Signal.date.desc()).limit(30)).all()
    holding = db.get(Holding, symbol)
    return CompanyDetail(
        company=company,
        prices=price_points,
        metrics=summary_metrics(prices, benchmark_prices),
        fundamentals=FundamentalsRead.model_validate(fundamentals) if fundamentals else None,
        signals=[_signal_read(signal) for signal in signals if _signal_read(signal) is not None],
        holding=_holding_read(db, holding) if holding else None,
    )


@router.put("/companies/{ticker}/fundamentals", response_model=FundamentalsRead)
def upsert_fundamentals(ticker: str, payload: FundamentalsUpsert, db: Session = Depends(get_db)) -> FundamentalsSnapshot:
    symbol = ticker.upper()
    ensure_company(db, symbol)
    snapshot_date = date.today()
    fundamentals = db.scalars(
        select(FundamentalsSnapshot).where(
            FundamentalsSnapshot.ticker == symbol,
            FundamentalsSnapshot.date == snapshot_date,
        )
    ).first()
    if fundamentals is None:
        fundamentals = FundamentalsSnapshot(ticker=symbol, date=snapshot_date)
        db.add(fundamentals)
    fundamentals.pe = payload.pe
    fundamentals.forward_pe = payload.forward_pe
    fundamentals.eps_growth = payload.eps_growth
    fundamentals.revenue_growth = payload.revenue_growth
    fundamentals.debt_to_equity = payload.debt_to_equity
    fundamentals.free_cash_flow = payload.free_cash_flow
    fundamentals.dividend_yield = payload.dividend_yield
    db.flush()
    recompute_signal(db, symbol)
    db.commit()
    db.refresh(fundamentals)
    return fundamentals


@router.get("/holdings", response_model=list[HoldingRead])
def list_holdings(db: Session = Depends(get_db)) -> list[HoldingRead]:
    holdings = db.scalars(select(Holding).order_by(Holding.ticker.asc())).all()
    return [_holding_read(db, holding) for holding in holdings]


@router.put("/holdings/{ticker}", response_model=HoldingRead)
def upsert_holding(ticker: str, payload: HoldingUpsert, db: Session = Depends(get_db)) -> HoldingRead:
    symbol = ticker.upper()
    ensure_company(db, symbol)
    holding = db.get(Holding, symbol)
    if holding is None:
        holding = Holding(ticker=symbol)
        db.add(holding)
    holding.shares = payload.shares
    holding.average_cost = payload.average_cost
    holding.target_allocation = payload.target_allocation
    holding.notes = payload.notes
    db.flush()
    recompute_signal(db, symbol)
    db.commit()
    db.refresh(holding)
    return _holding_read(db, holding)


@router.delete("/holdings/{ticker}")
def delete_holding(ticker: str, db: Session = Depends(get_db)) -> dict[str, str]:
    symbol = ticker.upper()
    holding = db.get(Holding, symbol)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    db.delete(holding)
    db.flush()
    recompute_signal(db, symbol)
    db.commit()
    return {"status": "deleted"}


@router.get("/analytics/market", response_model=MarketComparison)
def compare_market(db: Session = Depends(get_db)) -> dict:
    return market_comparison(db)


@router.get("/analytics/portfolio", response_model=PortfolioAnalytics)
def analyze_portfolio(db: Session = Depends(get_db)) -> dict:
    return portfolio_analytics(db)


@router.post("/demo/import", response_model=DemoImportResult)
def import_demo_data(db: Session = Depends(get_db)) -> DemoImportResult:
    demo_dir = Path(__file__).resolve().parents[2] / "data" / "demo_csv"
    imported = import_demo_csvs(db, demo_dir)
    return DemoImportResult(imported=imported)


@router.get("/ollama/status", response_model=OllamaStatus)
def ollama_status() -> dict:
    return OllamaClient().status()


@router.post("/ollama/analyze/ticker/{ticker}", response_model=OllamaAnalysisRead)
def create_ticker_ollama_analysis(ticker: str, db: Session = Depends(get_db)) -> OllamaAnalysisRead:
    try:
        analysis = generate_ticker_analysis(db, ticker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OllamaAnalysisRead.model_validate(analysis)


@router.get("/ollama/analyze/ticker/{ticker}", response_model=list[OllamaAnalysisRead])
def list_ticker_ollama_analyses(ticker: str, db: Session = Depends(get_db)) -> list[OllamaAnalysisRead]:
    symbol = ticker.upper()
    if db.get(Company, symbol) is None:
        raise HTTPException(status_code=404, detail="Ticker not found")
    return [OllamaAnalysisRead.model_validate(item) for item in analysis_history(db, "ticker", symbol)]


@router.post("/ollama/analyze/portfolio", response_model=OllamaAnalysisRead)
def create_portfolio_ollama_analysis(db: Session = Depends(get_db)) -> OllamaAnalysisRead:
    try:
        analysis = generate_portfolio_analysis(db)
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OllamaAnalysisRead.model_validate(analysis)


@router.get("/ollama/analyze/portfolio", response_model=list[OllamaAnalysisRead])
def list_portfolio_ollama_analyses(db: Session = Depends(get_db)) -> list[OllamaAnalysisRead]:
    return [OllamaAnalysisRead.model_validate(item) for item in analysis_history(db, "portfolio")]
