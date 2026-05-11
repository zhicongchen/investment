from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS
from app.models import Company, FundamentalsSnapshot, Holding, OllamaAnalysis, PriceBar, Signal
from app.services.analytics import (
    BENCHMARK_TICKER,
    close_value,
    enrich_price_points,
    market_comparison,
    portfolio_analytics,
    prices_for,
    summary_metrics,
)
from app.services.scoring import decode_list

PROMPT_VERSION = "v1"
ANALYSIS_TYPE = "deep_analysis"


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout_seconds: float = OLLAMA_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def status(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"base_url": self.base_url, "model": self.model, "reachable": False, "error": str(exc)}
        return {"base_url": self.base_url, "model": self.model, "reachable": True, "error": None}

    def generate(self, prompt: str) -> str:
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama returned invalid JSON") from exc

        text = data.get("response")
        if not isinstance(text, str) or not text.strip():
            raise OllamaError("Ollama returned an empty response")
        return text.strip()


def latest_signal(db: Session, ticker: str) -> Optional[Signal]:
    return db.scalars(select(Signal).where(Signal.ticker == ticker).order_by(Signal.date.desc()).limit(1)).first()


def latest_fundamentals(db: Session, ticker: str) -> Optional[FundamentalsSnapshot]:
    return db.scalars(
        select(FundamentalsSnapshot)
        .where(FundamentalsSnapshot.ticker == ticker)
        .order_by(FundamentalsSnapshot.date.desc())
        .limit(1)
    ).first()


def analysis_history(db: Session, subject_type: str, ticker: Optional[str] = None) -> list[OllamaAnalysis]:
    query = select(OllamaAnalysis).where(OllamaAnalysis.subject_type == subject_type)
    if ticker is None:
        query = query.where(OllamaAnalysis.ticker.is_(None))
    else:
        query = query.where(OllamaAnalysis.ticker == ticker.upper())
    return db.scalars(query.order_by(OllamaAnalysis.created_at.desc())).all()


def generate_ticker_analysis(db: Session, ticker: str, client: Optional[OllamaClient] = None) -> OllamaAnalysis:
    symbol = ticker.upper()
    company = db.get(Company, symbol)
    if company is None:
        raise ValueError("Ticker not found")

    context = build_ticker_context(db, symbol)
    prompt = build_ticker_prompt(context)
    ollama = client or OllamaClient()
    content = ollama.generate(prompt)
    analysis = OllamaAnalysis(
        subject_type="ticker",
        ticker=symbol,
        model=ollama.model,
        prompt_version=PROMPT_VERSION,
        analysis_type=ANALYSIS_TYPE,
        data_snapshot_date=context.get("data_snapshot_date"),
        content=content,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def generate_portfolio_analysis(db: Session, client: Optional[OllamaClient] = None) -> OllamaAnalysis:
    context = build_portfolio_context(db)
    prompt = build_portfolio_prompt(context)
    ollama = client or OllamaClient()
    content = ollama.generate(prompt)
    analysis = OllamaAnalysis(
        subject_type="portfolio",
        ticker=None,
        model=ollama.model,
        prompt_version=PROMPT_VERSION,
        analysis_type=ANALYSIS_TYPE,
        data_snapshot_date=context.get("data_snapshot_date"),
        content=content,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def build_ticker_context(db: Session, ticker: str) -> dict[str, Any]:
    company = db.get(Company, ticker)
    prices = prices_for(db, ticker)
    benchmark_prices = prices_for(db, BENCHMARK_TICKER)
    metrics = summary_metrics(prices, benchmark_prices)
    enriched = enrich_price_points(prices)
    signal = latest_signal(db, ticker)
    fundamentals = latest_fundamentals(db, ticker)
    holding = db.get(Holding, ticker)
    latest_price = prices[-1] if prices else None
    latest_technical = enriched[-1] if enriched else {}

    recent_prices = []
    for price in prices[-30:]:
        recent_prices.append(
            {
                "date": price.date.isoformat(),
                "close": close_value(price),
                "volume": price.volume,
            }
        )

    return {
        "data_snapshot_date": latest_price.date if latest_price else date.today(),
        "company": {
            "ticker": company.ticker if company else ticker,
            "name": company.name if company else "",
            "sector": company.sector if company else "",
            "exchange": company.exchange if company else "",
        },
        "metrics": _jsonable(metrics),
        "fundamentals": _model_snapshot(fundamentals),
        "signal": _signal_snapshot(signal),
        "holding": _holding_snapshot(holding),
        "technical_snapshot": _jsonable(latest_technical),
        "recent_prices": recent_prices,
    }


def build_portfolio_context(db: Session) -> dict[str, Any]:
    analytics = portfolio_analytics(db)
    comparison = market_comparison(db)
    companies = db.scalars(select(Company).where(Company.active.is_(True)).order_by(Company.ticker.asc())).all()
    watchlist = []
    snapshot_date: Optional[date] = None
    benchmark_prices = prices_for(db, BENCHMARK_TICKER)
    for company in companies:
        prices = prices_for(db, company.ticker)
        latest = prices[-1] if prices else None
        if latest and (snapshot_date is None or latest.date > snapshot_date):
            snapshot_date = latest.date
        watchlist.append(
            {
                "ticker": company.ticker,
                "name": company.name,
                "metrics": _jsonable(summary_metrics(prices, benchmark_prices)),
                "signal": _signal_snapshot(latest_signal(db, company.ticker)),
            }
        )

    return {
        "data_snapshot_date": snapshot_date or date.today(),
        "portfolio": _jsonable(analytics),
        "market_comparison": {
            "benchmark": comparison["benchmark"],
            "tickers": comparison["tickers"],
            "correlation": _jsonable(comparison["correlation"]),
        },
        "watchlist": watchlist,
    }


def build_ticker_prompt(context: dict[str, Any]) -> str:
    return _prompt(
        "Create a deep investment research memo for the selected ticker.",
        context,
        [
            "Thesis",
            "Supporting Data",
            "Valuation and Fundamental Quality",
            "Technical Context",
            "Portfolio Fit",
            "Risks",
            "Watch Items",
            "Uncertainty and Missing Data",
        ],
    )


def build_portfolio_prompt(context: dict[str, Any]) -> str:
    return _prompt(
        "Create a portfolio and watchlist research memo.",
        context,
        [
            "Portfolio Thesis",
            "Allocation and Drift",
            "Risk and Benchmark Context",
            "Highest Priority Tickers",
            "Actions to Review",
            "Watch Items",
            "Uncertainty and Missing Data",
        ],
    )


def _prompt(task: str, context: dict[str, Any], sections: list[str]) -> str:
    section_text = "\n".join(f"- {section}" for section in sections)
    return f"""You are a careful investment research assistant analyzing local portfolio data.

Task: {task}

Rules:
- Use only the JSON context below. Do not invent facts, news, earnings details, or external data.
- If a data point is missing, say it is missing and explain how that limits confidence.
- Write concise markdown with these sections:
{section_text}
- Do not recommend trade execution. Frame any buy/sell/trim language as items for human review.
- Always include this exact disclaimer at the end: This analysis is educational decision support and is not financial advice.

JSON context:
{json.dumps(_jsonable(context), indent=2, sort_keys=True)}
"""


def _signal_snapshot(signal: Optional[Signal]) -> Optional[dict[str, Any]]:
    if signal is None:
        return None
    return {
        "date": signal.date.isoformat(),
        "action": signal.action,
        "score": signal.score,
        "confidence": signal.confidence,
        "reasons": decode_list(signal.reasons),
        "risk_flags": decode_list(signal.risk_flags),
    }


def _model_snapshot(fundamentals: Optional[FundamentalsSnapshot]) -> Optional[dict[str, Any]]:
    if fundamentals is None:
        return None
    return {
        "date": fundamentals.date.isoformat(),
        "pe": fundamentals.pe,
        "forward_pe": fundamentals.forward_pe,
        "eps_growth": fundamentals.eps_growth,
        "revenue_growth": fundamentals.revenue_growth,
        "debt_to_equity": fundamentals.debt_to_equity,
        "free_cash_flow": fundamentals.free_cash_flow,
        "dividend_yield": fundamentals.dividend_yield,
    }


def _holding_snapshot(holding: Optional[Holding]) -> Optional[dict[str, Any]]:
    if holding is None:
        return None
    return {
        "ticker": holding.ticker,
        "shares": holding.shares,
        "average_cost": holding.average_cost,
        "target_allocation": holding.target_allocation,
        "notes": holding.notes,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
