from __future__ import annotations

from datetime import date, timedelta

from options.config import PutsConfig, RankingConfig, SizingConfig
from options.pricing import implied_vol, put_delta, put_price
from options.puts import rank_candidates, select_puts
from shared.moomoo import OptionQuote, UnderlyingVol

TODAY = date(2026, 9, 24)


def quote(strike: float, bid: float, ask: float, dte: int = 30, delta: float | None = -0.25, oi: int = 500, ticker: str = "ABC", iv: float | None = 0.4) -> OptionQuote:
    return OptionQuote(
        code=f"US.{ticker}{strike}",
        ticker=ticker,
        expiry=TODAY + timedelta(days=dte),
        strike=strike,
        bid=bid,
        ask=ask,
        delta=delta,
        iv=iv,
        open_interest=oi,
        volume=10,
    )


def test_select_computes_yield_and_breakeven():
    candidates, rejected = select_puts(100.0, [quote(90, 1.95, 2.05)], PutsConfig(), TODAY)
    assert not rejected
    item = candidates[0]
    assert item.premium == 2.0
    assert abs(item.annualized_yield - (2.0 / 90 * 365 / 30)) < 1e-9
    assert item.breakeven == 88.0
    assert abs(item.otm_pct - 0.10) < 1e-9
    assert item.collateral == 9000


def test_filters_reject_bad_contracts():
    quotes = [
        quote(90, 1.95, 2.05, dte=3),  # dte too short
        quote(105, 6.0, 6.2),  # in the money
        quote(90, 1.0, 1.5),  # spread 40%
        quote(90, 1.95, 2.05, oi=5),  # illiquid
        quote(90, 1.95, 2.05, delta=-0.6),  # delta too large
        quote(90, 0.05, 0.06),  # bid too small
    ]
    candidates, rejected = select_puts(100.0, quotes, PutsConfig(), TODAY)
    assert candidates == []
    assert rejected == {
        "dte out of range": 1,
        "not out of the money": 1,
        "spread too wide": 1,
        "open interest too low": 1,
        "delta out of range": 1,
        "bid too low / no market": 1,
    }


def test_missing_greeks_are_estimated():
    candidates, _ = select_puts(100.0, [quote(92, 1.4, 1.5, delta=None, iv=None)], PutsConfig(), TODAY)
    assert len(candidates) == 1
    assert candidates[0].delta_estimated
    assert -0.35 <= candidates[0].delta <= -0.15


def test_sizing_suggests_contract_count():
    candidates, _ = select_puts(100.0, [quote(90, 1.95, 2.05)], PutsConfig(), TODAY, sizing=SizingConfig(max_collateral_per_trade=20000))
    assert candidates[0].contracts == 2


def test_rank_by_yield_and_limit_per_ticker():
    quotes = [quote(90, 1.95, 2.05), quote(88, 1.4, 1.5), quote(85, 1.0, 1.1), quote(91, 2.4, 2.6, ticker="XYZ")]
    candidates, _ = select_puts(100.0, quotes, PutsConfig(), TODAY)
    ranked = rank_candidates(candidates, RankingConfig(), top_per_ticker=2)
    assert [(item.ticker, item.strike) for item in ranked] == [("XYZ", 91), ("ABC", 90), ("ABC", 88)]


def test_rank_by_iv_rank_uses_underlying_vol():
    a, _ = select_puts(100.0, [quote(90, 1.95, 2.05, ticker="A")], PutsConfig(), TODAY, UnderlyingVol(iv_rank=20))
    b, _ = select_puts(100.0, [quote(90, 1.0, 1.1, ticker="B")], PutsConfig(), TODAY, UnderlyingVol(iv_rank=80))
    ranked = rank_candidates(a + b, RankingConfig(metric="iv_rank"), top_per_ticker=3)
    assert [item.ticker for item in ranked] == ["B", "A"]


def test_blend_handles_missing_metrics():
    a, _ = select_puts(100.0, [quote(90, 1.95, 2.05, ticker="A")], PutsConfig(), TODAY, UnderlyingVol(iv_rank=90, iv=0.5, hv_30d=0.25))
    b, _ = select_puts(100.0, [quote(90, 1.0, 1.1, ticker="B")], PutsConfig(), TODAY)
    ranked = rank_candidates(a + b, RankingConfig(metric="blend"), top_per_ticker=3)
    assert ranked[0].ticker == "A"
    assert 0 <= ranked[1].score <= 100


def test_black_scholes_round_trip():
    price = put_price(100, 90, 30 / 365, 0.04, 0.4)
    assert abs(implied_vol(price, 100, 90, 30 / 365, 0.04) - 0.4) < 1e-4
    delta = put_delta(100, 90, 30 / 365, 0.04, 0.4)
    assert -0.3 < delta < -0.1
