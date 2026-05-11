from datetime import date, timedelta

from app.models import FundamentalsSnapshot, Holding, PriceBar
from app.services.scoring import compute_signal


def prices(days: int, start: float = 100, step: float = 0.5) -> list[PriceBar]:
    first = date(2024, 1, 1)
    return [
        PriceBar(
            ticker="TST",
            date=first + timedelta(days=i),
            open=start + i * step,
            high=start + i * step + 1,
            low=start + i * step - 1,
            close=start + i * step,
            adjusted_close=start + i * step,
            volume=1_000_000,
        )
        for i in range(days)
    ]


def fundamentals(**overrides) -> FundamentalsSnapshot:
    values = {
        "ticker": "TST",
        "date": date(2024, 12, 31),
        "pe": 18,
        "forward_pe": 16,
        "eps_growth": 0.12,
        "revenue_growth": 0.08,
        "debt_to_equity": 40,
        "free_cash_flow": 1_000_000_000,
        "dividend_yield": 0.01,
    }
    values.update(overrides)
    return FundamentalsSnapshot(**values)


def test_buy_signal_for_attractive_unowned_company():
    decision = compute_signal("TST", prices(240), fundamentals(forward_pe=14, eps_growth=0.2), None, 0)

    assert decision.action == "BUY"
    assert decision.score >= 72
    assert decision.risk_flags == []


def test_hold_signal_for_owned_position_near_target():
    holding = Holding(ticker="TST", shares=10, average_cost=90, target_allocation=1.0)
    decision = compute_signal("TST", prices(240), fundamentals(), holding, portfolio_value=2195)

    assert decision.action == "HOLD"
    assert "Position is near target allocation" in decision.reasons


def test_trim_signal_when_position_above_target():
    holding = Holding(ticker="TST", shares=10, average_cost=90, target_allocation=0.2)
    decision = compute_signal("TST", prices(240), fundamentals(), holding, portfolio_value=3000)

    assert decision.action == "TRIM"
    assert any("above target allocation" in flag for flag in decision.risk_flags)


def test_sell_review_for_owned_severely_stretched_valuation():
    holding = Holding(ticker="TST", shares=5, average_cost=100, target_allocation=0.5)
    decision = compute_signal("TST", prices(240), fundamentals(forward_pe=80), holding, portfolio_value=2200)

    assert decision.action == "SELL_REVIEW"
    assert any("severely stretched" in flag for flag in decision.risk_flags)


def test_watch_when_missing_fundamentals_and_short_history():
    decision = compute_signal("TST", prices(20), None, None, 0)

    assert decision.action == "WATCH"
    assert decision.confidence == "low"
    assert "Fundamentals unavailable" in decision.risk_flags

