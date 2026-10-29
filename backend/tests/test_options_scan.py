from datetime import date, timedelta

import pytest

from app.services import options_scan
from options.config import OptionsConfig
from options.edge import Underlying
from shared.moomoo import DailyBar, OptionQuote, UnderlyingVol

TODAY = date.today()


class FakeMarket:
    """Moomoo-shaped data: GOOD has fallen 15%, FLAT has not; both list one 30-day put."""

    def __init__(self):
        self.connected = False
        self.groups_requested = False

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def watchlist_tickers(self, groups):
        self.groups_requested = True
        return {"Everything": ["SHOULD_NOT_APPEAR"]}

    def stock_snapshots(self, tickers):
        prices = {"GOOD": 85.0, "FLAT": 100.0}
        return {t: {"last_price": prices[t], "prev_close": prices[t], "update_time": f"{TODAY} 11:00:00"} for t in tickers}

    def daily_bars(self, ticker, start):
        first = TODAY - timedelta(days=60)
        return [DailyBar(date=first + timedelta(days=i), high=100.0, close=100.0 * (1 + 0.01 * (-1) ** i)) for i in range(60)]

    def underlying_vol(self, tickers):
        return {t: UnderlyingVol(iv=0.45, hv_30d=0.25) for t in tickers}

    def put_chain_codes(self, ticker, first, last):
        strike = 75.0 if ticker == "GOOD" else 90.0
        return [(f"{ticker}{strike:g}P", TODAY + timedelta(days=30), strike)]

    def option_quotes(self, ticker, contracts):
        return [
            OptionQuote(code=code, ticker=ticker, expiry=expiry, strike=strike, bid=1.0, ask=1.05, delta=-0.18, iv=0.45, open_interest=500)
            for code, expiry, strike in contracts
        ]

    def next_earnings(self, ticker):
        return TODAY + timedelta(days=90)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(options_scan, "OPTIONS_REPORT_DIR", tmp_path / "reports")
    config = OptionsConfig()
    config.storage.path = str(tmp_path / "options.db")
    config.ranking.metric = "put_score"
    return config, tmp_path


def test_scan_grades_requested_tickers_only_and_writes_reports(isolated):
    config, tmp_path = isolated
    market = FakeMarket()
    verdicts = {"GOOD": Underlying(action="BUY", composite_score=80, fair_value=120.0, buy_below=96.0)}

    payload = options_scan.run_options_scan(
        ["good", "FLAT"], client=market, config=config, fundamentals=lambda tickers: {t: verdicts[t] for t in tickers if t in verdicts}
    )

    assert payload["universe"] == ["FLAT", "GOOD"]
    assert {item["ticker"] for item in payload["candidates"]} == {"GOOD", "FLAT"}  # all tickers, not only dips
    good = next(item for item in payload["candidates"] if item["ticker"] == "GOOD")
    assert good["grade"] in {"A", "B"} and good["verdict"] == "BUY" and good["reasons"]
    assert payload["underlyings"]["GOOD"]["action"] == "BUY"
    assert not market.groups_requested
    assert options_scan.latest_scan()["generated_at"] == payload["generated_at"]
    assert (tmp_path / "options.db").exists()


def test_only_dipping_skips_flat_stocks(isolated):
    config, _ = isolated

    payload = options_scan.run_options_scan(["GOOD", "FLAT"], only_dipping=True, client=FakeMarket(), config=config)

    assert {item["ticker"] for item in payload["candidates"]} == {"GOOD"}


def test_concurrent_scans_are_refused(isolated):
    config, _ = isolated
    assert options_scan._scan_lock.acquire(blocking=False)
    try:
        with pytest.raises(options_scan.ScanBusyError):
            options_scan.run_options_scan(["GOOD"], client=FakeMarket(), config=config)
    finally:
        options_scan._scan_lock.release()


def test_dashboard_fundamentals_falls_back_to_live_for_unknown_tickers(monkeypatch):
    analysis = {
        "composite_score": 70,
        "recommendation": {"action": "HOLD", "conviction": "medium"},
        "valuation": {"base_value": 150.0, "entry_price": 120.0},
    }
    live_calls = []
    monkeypatch.setattr(
        options_scan,
        "fundamentals_lookup",
        lambda mode: (lambda tickers: live_calls.append(tickers) or {t: Underlying(action="WATCH") for t in tickers}),
    )

    lookup = options_scan.dashboard_fundamentals(lambda ticker: analysis if ticker == "KNOWN" else None)
    views = lookup(["KNOWN", "NEW"])

    assert views["KNOWN"].action == "HOLD" and views["KNOWN"].fair_value == 150.0 and views["KNOWN"].buy_below == 120.0
    assert views["NEW"].action == "WATCH"
    assert live_calls == [["NEW"]]


def test_scan_endpoint(isolated, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api import router
    from app.db import Base, get_db
    from app.models import Company

    config, _ = isolated
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Company(ticker="GOOD", name="Good Inc.", active=True))
    db.commit()
    monkeypatch.setattr(options_scan, "load_config", lambda: config)
    monkeypatch.setattr(options_scan, "_client", lambda source: (FakeMarket(), "yahoo"))
    monkeypatch.setattr(options_scan, "is_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(options_scan, "fundamentals_lookup", lambda mode: (lambda tickers: {}))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    defaults = client.get("/api/options/defaults").json()
    response = client.post("/api/options/scan", json={"tickers": ["GOOD", "FLAT"], "source": "auto"})

    assert defaults["tickers"] == ["GOOD"] and defaults["moomoo_available"] is False
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "yahoo"
    assert body["candidates"]
    assert any("Yahoo Finance" in warning for warning in body["warnings"])
    assert client.get("/api/options/latest").json()["generated_at"] == body["generated_at"]
    assert client.post("/api/options/scan", json={"tickers": []}).status_code == 422
