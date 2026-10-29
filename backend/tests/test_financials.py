import math
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.providers.base import FinancialStatementData
from app.services.financials import (
    _Period,
    _split_periods,
    _trailing_twelve_months,
    altman_z_score,
    analyze_company,
    dcf_enterprise_value,
    implied_growth,
    piotroski_f_score,
)


def statement(year: int, revenue: float, margin: float = 0.30, shares: float = 1_000e6, period: str = "annual", fiscal_date=None, **overrides):
    operating_income = revenue * margin
    pretax = operating_income - 0.5e9
    net_income = pretax * 0.8
    values = dict(
        revenue=revenue,
        cost_of_revenue=revenue * 0.4,
        operating_income=operating_income,
        ebitda=operating_income * 1.15,
        interest_expense=0.8e9,
        pretax_income=pretax,
        income_tax=pretax * 0.2,
        net_income=net_income,
        diluted_eps=net_income / shares,
        operating_cash_flow=operating_income * 0.95,
        capital_expenditure=revenue * 0.05,
        dividends_paid=2e9,
        share_repurchases=5e9,
        stock_based_compensation=revenue * 0.02,
        depreciation=operating_income * 0.15,
        diluted_shares=shares,
        shares_outstanding=shares,
        total_assets=revenue * 1.5,
        current_assets=revenue * 0.6,
        cash_and_investments=30e9,
        receivables=revenue * 0.1,
        inventory=revenue * 0.02,
        total_liabilities=revenue * 0.7,
        current_liabilities=revenue * 0.3,
        total_debt=20e9,
        long_term_debt=18e9,
        shareholders_equity=revenue * 0.8,
        retained_earnings=revenue * 0.5,
    )
    values.update(overrides)
    return FinancialStatementData(fiscal_date=fiscal_date or date(year, 12, 31), period=period, **values)


def compounder(margin_step: float = 0.01) -> list[FinancialStatementData]:
    """Five years of 12% revenue growth, expanding margins and a shrinking share count."""
    return [statement(2021 + i, 80e9 * 1.12**i, 0.26 + margin_step * i, shares=1_000e6 * 0.98**i) for i in range(5)]


def bars(level: float, days: int = 600, drift: float = 0.0004):
    first = date(2024, 1, 1)
    out = []
    for i in range(days):
        close = level * math.exp(drift * (i - days + 1) + 0.01 * math.sin(i / 5))
        out.append(SimpleNamespace(date=first + timedelta(days=i), close=close, adjusted_close=close))
    return out


def analyze(statements, price_level, **kwargs):
    return analyze_company(
        ticker="TST",
        sector=kwargs.pop("sector", "Technology"),
        statements=statements,
        prices=bars(price_level, drift=kwargs.pop("drift", 0.0004)),
        benchmark_prices=bars(500, drift=0.0003),
        **kwargs,
    )


def test_trailing_twelve_months_sums_quarters_and_keeps_latest_balance():
    quarters = [
        statement(0, 10e9, period="quarterly", fiscal_date=day, total_assets=assets)
        for day, assets in [
            (date(2025, 3, 31), 100e9),
            (date(2025, 6, 30), 101e9),
            (date(2025, 9, 30), 102e9),
            (date(2025, 12, 31), 103e9),
        ]
    ]
    annual, quarterly = _split_periods([statement(2024, 35e9)] + quarters)

    ttm, basis = _trailing_twelve_months(annual, quarterly)

    assert basis == "TTM"
    assert ttm.fiscal_date == date(2025, 12, 31)
    assert ttm["revenue"] == pytest.approx(40e9)
    assert ttm["total_assets"] == 103e9


def test_trailing_twelve_months_falls_back_to_fiscal_year_when_quarters_have_gaps():
    quarters = [statement(0, 10e9, period="quarterly", fiscal_date=day) for day in [date(2024, 9, 30), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30)]]
    annual, quarterly = _split_periods([statement(2024, 35e9)] + quarters)

    ttm, basis = _trailing_twelve_months(annual, quarterly)

    assert basis == "FY"
    assert ttm["revenue"] == 35e9


def test_dcf_with_no_growth_equals_perpetuity_value():
    value, terminal_share = dcf_enterprise_value(100, growth=0.0, discount_rate=0.10, terminal_growth=0.0)

    assert value == pytest.approx(1000)
    assert 0 < terminal_share < 1


def test_reverse_dcf_recovers_growth_used_to_price():
    target, _ = dcf_enterprise_value(100, growth=0.08, discount_rate=0.09, terminal_growth=0.025)

    assert implied_growth(target, 100, 0.09, 0.025) == pytest.approx(0.08, abs=1e-6)


def test_altman_z_matches_formula():
    period = _Period(
        fiscal_date=date(2025, 12, 31),
        values=dict(
            total_assets=100.0,
            total_liabilities=50.0,
            current_assets=40.0,
            current_liabilities=20.0,
            retained_earnings=30.0,
            operating_income=10.0,
            revenue=120.0,
        ),
    )

    z = altman_z_score(period, market_cap=200.0)

    assert z == pytest.approx(1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.1 + 0.6 * 4 + 1.0 * 1.2)


def test_piotroski_rewards_improving_fundamentals():
    annual, _ = _split_periods(compounder())

    result = piotroski_f_score(annual)

    assert result["max"] == 9
    assert result["score"] >= 6
    assert {"name": "No share dilution", "passed": True} in result["checks"]


def test_piotroski_penalizes_deteriorating_fundamentals():
    deteriorating = [
        statement(2023, 100e9, 0.20),
        statement(2024, 95e9, 0.12, shares=1_050e6, long_term_debt=30e9, operating_cash_flow=8e9, current_assets=40e9, cost_of_revenue=95e9 * 0.5),
        statement(2025, 90e9, 0.05, shares=1_100e6, long_term_debt=40e9, operating_cash_flow=-1e9, current_assets=30e9, cost_of_revenue=90e9 * 0.6),
    ]
    annual, _ = _split_periods(deteriorating)

    result = piotroski_f_score(annual)

    assert result["score"] <= 3


def test_quality_compounder_below_fair_value_is_a_buy():
    analysis = analyze(compounder(), price_level=120)

    recommendation = analysis["recommendation"]
    assert recommendation["action"] == "BUY"
    assert analysis["valuation"]["margin_of_safety"] > 0
    assert analysis["valuation"]["entry_price"] == pytest.approx(analysis["valuation"]["base_value"] * 0.8, rel=1e-3)
    assert analysis["economic_spread"] > 0
    assert recommendation["strengths"]
    assert not [flag for flag in analysis["flags"] if flag["severity"] == "high"]


def test_same_compounder_far_above_fair_value_waits_for_an_entry_price():
    analysis = analyze(compounder(), price_level=5000)

    recommendation = analysis["recommendation"]
    assert recommendation["action"] == "WATCH"
    assert analysis["valuation"]["margin_of_safety"] < 0
    assert any("Becomes a stronger buy below" in trigger for trigger in recommendation["triggers"])
    assert any("implies" in trigger for trigger in recommendation["triggers"])


def test_owned_expensive_position_is_trimmed():
    holding = SimpleNamespace(shares=10, average_cost=100, target_allocation=0.5)

    analysis = analyze(compounder(), price_level=5000, holding=holding, portfolio_value=100_000)

    assert analysis["recommendation"]["action"] == "TRIM"
    assert analysis["position"]["unrealized_return"] > 0


def test_owned_overweight_position_is_trimmed_even_when_cheap():
    holding = SimpleNamespace(shares=100, average_cost=100, target_allocation=0.10)

    analysis = analyze(compounder(), price_level=120, holding=holding, portfolio_value=20_000)

    assert analysis["position"]["overweight"] is True
    assert analysis["recommendation"]["action"] == "TRIM"


def test_owned_cheap_position_below_target_is_an_add():
    holding = SimpleNamespace(shares=10, average_cost=100, target_allocation=0.5)

    analysis = analyze(compounder(), price_level=120, holding=holding, portfolio_value=10_000)

    assert analysis["recommendation"]["action"] == "ADD"


def distressed() -> list[FinancialStatementData]:
    return [
        statement(
            2021 + i,
            revenue,
            margin,
            shares=1_000e6 * 1.08**i,
            operating_cash_flow=-2e9,
            total_debt=60e9,
            long_term_debt=55e9,
            cash_and_investments=2e9,
            current_assets=revenue * 0.2,
            current_liabilities=revenue * 0.4,
            total_liabilities=revenue * 1.4,
            shareholders_equity=revenue * 0.1,
            retained_earnings=-revenue * 0.5,
            interest_expense=4e9,
        )
        for i, (revenue, margin) in enumerate([(50e9, 0.06), (46e9, 0.03), (42e9, 0.01), (38e9, -0.02)])
    ]


def test_distressed_company_is_avoided_with_red_flags():
    analysis = analyze(distressed(), price_level=10, drift=-0.001)

    severities = {flag["severity"] for flag in analysis["flags"]}
    assert analysis["recommendation"]["action"] == "AVOID"
    assert "high" in severities
    assert analysis["valuation"]["applicable"] is False
    assert any("diluted" in flag["message"] for flag in analysis["flags"])


def test_owned_distressed_company_is_flagged_for_sell_review():
    holding = SimpleNamespace(shares=10, average_cost=20, target_allocation=0.1)

    analysis = analyze(distressed(), price_level=10, drift=-0.001, holding=holding, portfolio_value=1_000)

    assert analysis["recommendation"]["action"] == "SELL_REVIEW"


def test_financial_sector_uses_justified_price_to_book():
    analysis = analyze(compounder(), price_level=120, sector="Financial Services")

    assert analysis["valuation"]["method"].startswith("Justified price-to-book")
    assert analysis["altman_z"] is None
    assert any("Financial-sector" in warning for warning in analysis["warnings"])


def test_missing_statements_reports_insufficient_data():
    analysis = analyze([], price_level=100)

    assert analysis["recommendation"]["action"] == "INSUFFICIENT_DATA"
    assert analysis["recommendation"]["conviction"] == "low"
    assert analysis["composite_score"] is None
    assert analysis["recommendation"]["strengths"] == []
    assert analysis["warnings"]


def test_trailing_twelve_months_used_when_last_quarter_closes_the_fiscal_year():
    quarters = [statement(0, 10e9, period="quarterly", fiscal_date=day) for day in [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)]]
    annual, quarterly = _split_periods([statement(2025, 40e9)] + quarters)

    _, basis = _trailing_twelve_months(annual, quarterly)

    assert basis == "TTM"


def test_heavy_capital_spending_normalizes_cash_flow_from_nopat():
    investing = [
        statement(2021 + i, 80e9 * 1.12**i, 0.30, shares=1_000e6, capital_expenditure=80e9 * 1.12**i * 0.25)
        for i in range(5)
    ]

    analysis = analyze(investing, price_level=120)

    valuation = analysis["valuation"]
    assert valuation["applicable"] is True
    assert valuation["cash_flow_basis"].startswith("Normalized")
    reported_fcf = investing[-1].operating_cash_flow - investing[-1].capital_expenditure
    assert valuation["base_cash_flow"] > 2 * reported_fcf
    assert analysis["recommendation"]["conviction"] != "high"


def test_negative_forward_growth_is_ignored_when_sales_are_growing():
    fundamentals = SimpleNamespace(pe=16.0, forward_pe=21.0, revenue_growth=0.12, dividend_yield=None)

    analysis = analyze(compounder(), price_level=120, fundamentals=fundamentals)

    assert "forward" not in analysis["valuation"]["growth_source"]
    assert analysis["valuation"]["stage_one_growth"] > 0.08
