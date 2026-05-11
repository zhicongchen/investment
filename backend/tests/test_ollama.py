from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import create_app
from app.models import Company, FundamentalsSnapshot, Holding, OllamaAnalysis, PriceBar, Signal
from app.services.ollama import (
    OllamaError,
    build_portfolio_context,
    build_ticker_context,
    generate_portfolio_analysis,
    generate_ticker_analysis,
)


class FakeOllamaClient:
    model = "fake-local-model"

    def __init__(self, text: str = "## Thesis\nLocal analysis\n\nThis analysis is educational decision support and is not financial advice.") -> None:
        self.text = text

    def generate(self, prompt: str) -> str:
        assert "JSON context" in prompt
        return self.text


class FailingOllamaClient:
    model = "fake-local-model"

    def generate(self, prompt: str) -> str:
        raise OllamaError("Ollama request failed")


def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def seed_data(db):
    first = date(2024, 1, 1)
    db.add(Company(ticker="TST", name="Test Corp", sector="Technology", exchange="NMS", active=True))
    db.add(Company(ticker="SPY", name="SPY", active=False))
    for index in range(220):
        db.add(
            PriceBar(
                ticker="TST",
                date=first + timedelta(days=index),
                open=100 + index,
                high=101 + index,
                low=99 + index,
                close=100 + index,
                adjusted_close=100 + index,
                volume=1000 + index,
            )
        )
        db.add(
            PriceBar(
                ticker="SPY",
                date=first + timedelta(days=index),
                open=100 + index * 0.5,
                high=101 + index * 0.5,
                low=99 + index * 0.5,
                close=100 + index * 0.5,
                adjusted_close=100 + index * 0.5,
                volume=2000 + index,
            )
        )
    db.add(
        FundamentalsSnapshot(
            ticker="TST",
            date=date(2024, 12, 31),
            pe=18,
            forward_pe=16,
            eps_growth=0.12,
            revenue_growth=0.09,
            debt_to_equity=30,
            free_cash_flow=100_000_000,
            dividend_yield=0.01,
        )
    )
    db.add(Holding(ticker="TST", shares=3, average_cost=120, target_allocation=0.5, notes="Core test position"))
    db.add(
        Signal(
            ticker="TST",
            date=first + timedelta(days=219),
            action="HOLD",
            score=74,
            confidence="high",
            reasons='["Strong trend"]',
            risk_flags='["Valuation watch"]',
        )
    )
    db.commit()


def test_builds_ticker_context_from_collected_data():
    db = session()
    seed_data(db)

    context = build_ticker_context(db, "TST")

    assert context["company"]["name"] == "Test Corp"
    assert context["fundamentals"]["forward_pe"] == 16
    assert context["signal"]["action"] == "HOLD"
    assert context["technical_snapshot"]["ma200"] is not None
    assert len(context["recent_prices"]) == 30


def test_builds_portfolio_context_from_holdings_and_watchlist():
    db = session()
    seed_data(db)

    context = build_portfolio_context(db)

    assert context["portfolio"]["total_value"] > 0
    assert context["portfolio"]["allocation"][0]["ticker"] == "TST"
    assert context["watchlist"][0]["ticker"] == "TST"
    assert context["market_comparison"]["benchmark"] == "SPY"


def test_generate_saves_history_newest_first():
    db = session()
    seed_data(db)

    first = generate_ticker_analysis(db, "TST", FakeOllamaClient("first analysis"))
    second = generate_ticker_analysis(db, "TST", FakeOllamaClient("second analysis"))
    items = db.scalars(select(OllamaAnalysis).order_by(OllamaAnalysis.created_at.desc())).all()

    assert first.id != second.id
    assert items[0].content == "second analysis"
    assert items[1].content == "first analysis"


def test_generate_portfolio_analysis_saves_result():
    db = session()
    seed_data(db)

    analysis = generate_portfolio_analysis(db, FakeOllamaClient("portfolio analysis"))

    assert analysis.subject_type == "portfolio"
    assert analysis.ticker is None
    assert analysis.content == "portfolio analysis"


def test_api_handles_missing_ticker_and_ollama_failure(monkeypatch):
    db = session()
    seed_data(db)
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db

    def fail_generate(db, ticker):
        raise OllamaError("Ollama request failed")

    monkeypatch.setattr("app.api.generate_ticker_analysis", fail_generate)
    client = TestClient(app)

    assert client.get("/api/ollama/analyze/ticker/MISSING").status_code == 404
    response = client.post("/api/ollama/analyze/ticker/TST")

    assert response.status_code == 502
    assert "Ollama request failed" in response.text
