from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from options.config import EdgeConfig, OptionsConfig
from options.edge import Underlying, evaluate, excluded, prob_above, realized_vol
from options.puts import PutCandidate
from options.scanner import ET, run_scan
from options.store import OptionsStore
from options.yahoo import YahooOptionsClient, next_earnings_date
from shared.moomoo import DailyBar, OptionQuote, UnderlyingVol

TODAY = date(2026, 9, 24)
QUALITY = Underlying(action="BUY", conviction="high", composite_score=80, fair_value=120.0, buy_below=96.0)


def candidate(strike: float = 90.0, premium: float = 1.6, iv: float = 0.40, dte: int = 30, spot: float = 100.0) -> PutCandidate:
    return PutCandidate(
        ticker="TST",
        code=f"TST{strike:g}P",
        expiry=TODAY + timedelta(days=dte),
        dte=dte,
        strike=strike,
        spot=spot,
        bid=premium - 0.05,
        ask=premium + 0.05,
        premium=premium,
        spread_pct=0.1 / premium,
        delta=-0.2,
        delta_estimated=False,
        iv=iv,
        open_interest=1000,
        volume=100,
        otm_pct=1 - strike / spot,
        annualized_yield=premium / strike * 365 / dte,
        breakeven=strike - premium,
        cushion_pct=(spot - strike + premium) / spot,
        collateral=strike * 100,
    )


def test_prob_above_is_half_at_the_money_without_drift():
    assert prob_above(100, 100, 1.0, 0.0, 1e-9 + 0.2) == pytest.approx(0.46, abs=0.01)  # N(-sigma/2)
    assert prob_above(100, 80, 30 / 365, 0.04, 0.3) > 0.95


def test_realized_vol_matches_constant_daily_moves():
    closes = [100 * math.exp(0.01 * ((-1) ** i)) for i in range(40)]  # +/-1% log moves
    assert realized_vol(closes, 30) == pytest.approx(0.02 * math.sqrt(252) * math.sqrt(30 / 29), rel=0.02)


def test_rich_put_on_quality_stock_grades_well():
    item = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.25, underlying=QUALITY, is_dip=True)

    assert item.grade in {"A", "B"}
    assert item.contract_iv_hv == pytest.approx(1.6)
    assert item.edge > 0
    assert 0.8 < item.prob_profit < 1
    assert any("Premium is rich" in reason for reason in item.reasons)
    assert any("buy-below" in reason for reason in item.reasons)


def test_cheap_put_scores_below_rich_put():
    rich = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.25, underlying=QUALITY)
    cheap = evaluate(candidate(iv=0.22, premium=0.5), EdgeConfig(), 0.04, hv=0.30, underlying=QUALITY)

    assert cheap.put_score < rich.put_score
    assert any("below realized volatility" in caution for caution in cheap.cautions)


def test_earnings_inside_the_trade_is_penalized_and_capped():
    clean = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.25, underlying=QUALITY)
    risky = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.25, underlying=QUALITY, earnings=TODAY + timedelta(days=10))

    assert risky.earnings_in_trade
    assert risky.put_score == pytest.approx(clean.put_score - 15, abs=0.2)
    assert risky.grade != "A"
    assert excluded(risky, EdgeConfig(earnings="exclude")) == "earnings before expiry"
    after_expiry = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.25, earnings=TODAY + timedelta(days=60))
    assert not after_expiry.earnings_in_trade


def test_avoid_verdict_is_excluded():
    item = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.25, underlying=Underlying(action="AVOID", composite_score=30))

    assert item.grade == "AVOID"
    assert excluded(item, EdgeConfig()) == "fundamentals: avoid"
    assert excluded(item, EdgeConfig(exclude_fundamental_avoid=False)) is None


def test_missing_fundamentals_caps_grade_and_says_so():
    item = evaluate(candidate(), EdgeConfig(), 0.04, hv=0.20)

    assert item.grade != "A"
    assert any("No fundamental check" in caution for caution in item.cautions)


class FakeData:
    """GOOD and BAD both fell 15%; BAD's fundamentals say AVOID."""

    def watchlist_tickers(self, groups):
        return {"Tech": ["GOOD", "BAD"]}

    def stock_snapshots(self, tickers):
        return {t: {"last_price": 85.0, "prev_close": 86.0, "update_time": f"{TODAY} 11:00:00"} for t in tickers}

    def daily_bars(self, ticker, start):
        first = TODAY - timedelta(days=60)
        return [DailyBar(date=first + timedelta(days=i), high=100.0, close=100.0 * (1 + 0.01 * (-1) ** i)) for i in range(60)]

    def underlying_vol(self, tickers):
        return {t: UnderlyingVol(iv=0.45, hv_30d=0.25) for t in tickers}

    def put_chain_codes(self, ticker, first, last):
        return [(f"{ticker}75P", TODAY + timedelta(days=30), 75.0)]

    def option_quotes(self, ticker, contracts):
        return [OptionQuote(code=code, ticker=ticker, expiry=expiry, strike=strike, bid=1.0, ask=1.05, delta=-0.18, iv=0.45, open_interest=500) for code, expiry, strike in contracts]


def test_scan_scores_candidates_and_drops_avoid_verdicts():
    config = OptionsConfig()
    config.ranking.metric = "put_score"
    config.ibkr.enabled = False
    views = {"GOOD": QUALITY, "BAD": Underlying(action="AVOID", composite_score=25)}

    result = run_scan(
        config,
        FakeData(),
        now=datetime(2026, 9, 24, 11, 0, tzinfo=ET),
        earnings=lambda ticker: TODAY + timedelta(days=90),
        fundamentals=lambda tickers: {ticker: views[ticker] for ticker in tickers},
    )

    assert [item.ticker for item in result.candidates] == ["GOOD"]
    assert result.candidates[0].grade in {"A", "B"}
    assert result.candidates[0].hv_30d is not None
    assert result.rejections["BAD"]["fundamentals: avoid"] == 1


def test_all_tickers_mode_scans_stocks_that_are_not_dipping():
    class Flat(FakeData):
        def stock_snapshots(self, tickers):
            return {t: {"last_price": 100.0, "prev_close": 100.0, "update_time": f"{TODAY} 11:00:00"} for t in tickers}

    config = OptionsConfig()
    config.ibkr.enabled = False
    assert run_scan(config, Flat(), now=datetime(2026, 9, 24, 11, 0, tzinfo=ET)).candidates == []
    config.dip.required = False
    assert run_scan(config, Flat(), now=datetime(2026, 9, 24, 11, 0, tzinfo=ET)).candidates


class FakeYfTicker:
    options = ("2026-10-02", "2026-10-23", "2027-01-15")

    def __init__(self, symbol):
        self.symbol = symbol
        self.calendar = {"Earnings Date": [date(2026, 10, 29)]}

    def history(self, period=None, start=None, end=None, auto_adjust=True):
        index = pd.date_range("2026-08-01", periods=40, freq="B")
        closes = [100 + (i % 3) for i in range(40)]
        return pd.DataFrame({"Open": closes, "High": [c + 1 for c in closes], "Low": closes, "Close": closes, "Volume": 1000}, index=index)

    def option_chain(self, expiry):
        puts = pd.DataFrame(
            {
                "contractSymbol": [f"TST{expiry}P90", f"TST{expiry}P95", f"TST{expiry}P100"],
                "strike": [90.0, 95.0, 100.0],
                "bid": [0.8, 0.0, 2.9],
                "ask": [0.9, 0.0, 3.1],
                "lastPrice": [0.85, 1.4, 3.0],
                "impliedVolatility": [0.35, 0.00001, 0.30],
                "openInterest": [500, 200, 900],
                "volume": [10, 0, 50],
            }
        )
        return type("Chain", (), {"puts": puts})()


class FakeYf:
    Ticker = FakeYfTicker


def test_yahoo_client_maps_chains_quotes_and_earnings():
    client = YahooOptionsClient(yf=FakeYf())
    client.connect()

    codes = client.put_chain_codes("TST", date(2026, 9, 30), date(2026, 11, 7))
    quotes = {quote.strike: quote for quote in client.option_quotes("TST", codes)}

    assert {expiry for _, expiry, _ in codes} == {date(2026, 10, 2), date(2026, 10, 23)}
    assert quotes[90.0].bid == 0.8 and quotes[90.0].iv == 0.35 and quotes[90.0].open_interest == 500
    assert quotes[95.0].bid == quotes[95.0].ask == 1.4  # no bid/ask: last trade stands in
    assert quotes[95.0].iv is None  # placeholder IV dropped
    assert client.used_last_trade
    assert client.stock_snapshots(["TST"])["TST"]["last_price"] == 100 + 39 % 3
    assert next_earnings_date("TST", FakeYfTicker("TST"), today=date(2026, 10, 1)) == date(2026, 10, 29)
    assert next_earnings_date("TST", FakeYfTicker("TST"), today=date(2026, 11, 1)) is None


def test_store_adds_put_score_columns_to_old_databases(tmp_path):
    path = tmp_path / "options.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE scan_candidates (scan_id INTEGER, rank INTEGER, ticker TEXT, code TEXT, expiry TEXT, dte INTEGER, "
                     "strike REAL, spot REAL, bid REAL, ask REAL, premium REAL, delta REAL, delta_estimated INTEGER, iv REAL, "
                     "iv_rank REAL, iv_hv_ratio REAL, open_interest INTEGER, otm_pct REAL, annualized_yield REAL, score REAL)")

    OptionsStore(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scan_candidates)")}
    assert {"put_score", "grade", "prob_profit", "verdict"} <= columns
