from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.models import FundamentalsSnapshot, Holding, PriceBar


@dataclass(frozen=True)
class SignalDecision:
    date: date
    action: str
    score: float
    confidence: str
    reasons: list[str]
    risk_flags: list[str]


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _latest_close(prices: list[PriceBar]) -> Optional[float]:
    for price in reversed(prices):
        if price.adjusted_close is not None:
            return price.adjusted_close
        if price.close is not None:
            return price.close
    return None


def _moving_average(prices: list[PriceBar], window: int) -> Optional[float]:
    closes = [(p.adjusted_close if p.adjusted_close is not None else p.close) for p in prices]
    closes = [p for p in closes if p is not None]
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def compute_signal(
    ticker: str,
    prices: list[PriceBar],
    fundamentals: Optional[FundamentalsSnapshot],
    holding: Optional[Holding],
    portfolio_value: float,
) -> SignalDecision:
    if not prices:
        return SignalDecision(date=date.today(), action="WATCH", score=0, confidence="low", reasons=[], risk_flags=["No price history available"])

    latest_date = prices[-1].date
    latest_close = _latest_close(prices)
    reasons: list[str] = []
    risk_flags: list[str] = []

    valuation = 50.0
    quality = 50.0
    growth = 50.0
    trend = 50.0
    portfolio_fit = 50.0

    if fundamentals:
        pe = fundamentals.forward_pe or fundamentals.pe
        if pe is None:
            risk_flags.append("Valuation data unavailable")
        elif pe <= 15:
            valuation = 85
            reasons.append("Valuation appears attractive versus a 15x earnings reference")
        elif pe <= 25:
            valuation = 70
            reasons.append("Valuation is reasonable versus earnings")
        elif pe <= 40:
            valuation = 45
            risk_flags.append("Valuation is elevated versus earnings")
        else:
            valuation = 20
            risk_flags.append("Valuation is severely stretched versus earnings")

        if fundamentals.debt_to_equity is None:
            risk_flags.append("Debt data unavailable")
        elif fundamentals.debt_to_equity <= 75:
            quality += 25
            reasons.append("Debt-to-equity is within a conservative range")
        elif fundamentals.debt_to_equity > 200:
            quality -= 25
            risk_flags.append("Debt-to-equity is high")

        if fundamentals.free_cash_flow is not None and fundamentals.free_cash_flow > 0:
            quality += 20
            reasons.append("Free cash flow is positive")
        elif fundamentals.free_cash_flow is not None:
            quality -= 20
            risk_flags.append("Free cash flow is negative")

        growth_inputs = [g for g in [fundamentals.eps_growth, fundamentals.revenue_growth] if g is not None]
        if growth_inputs:
            avg_growth = sum(growth_inputs) / len(growth_inputs)
            if avg_growth >= 0.15:
                growth = 85
                reasons.append("Growth metrics are strong")
            elif avg_growth >= 0.03:
                growth = 65
                reasons.append("Growth metrics are positive")
            elif avg_growth < 0:
                growth = 25
                risk_flags.append("Growth metrics are negative")
        else:
            risk_flags.append("Growth data unavailable")
    else:
        risk_flags.append("Fundamentals unavailable")

    ma50 = _moving_average(prices, 50)
    ma200 = _moving_average(prices, 200)
    if latest_close and ma50 and ma200:
        if latest_close >= ma50 >= ma200:
            trend = 85
            reasons.append("Price is above both 50-day and 200-day moving averages")
        elif latest_close >= ma200:
            trend = 65
            reasons.append("Price remains above the 200-day moving average")
        else:
            trend = 25
            risk_flags.append("Price is below the 200-day moving average")
    else:
        risk_flags.append("Not enough price history for long-term trend")

    if holding and latest_close and portfolio_value > 0:
        market_value = holding.shares * latest_close
        current_allocation = market_value / portfolio_value
        target = holding.target_allocation
        if target > 0 and current_allocation > target * 1.35:
            portfolio_fit = 20
            risk_flags.append("Position is materially above target allocation")
        elif target > 0 and current_allocation < target * 0.75:
            portfolio_fit = 80
            reasons.append("Position is below target allocation")
        else:
            portfolio_fit = 65
            reasons.append("Position is near target allocation")
    elif holding:
        portfolio_fit = 60
        reasons.append("Holding exists but allocation cannot be fully evaluated yet")
    else:
        portfolio_fit = 60
        reasons.append("No current holding recorded")

    quality = _clamp(quality)
    score = round(
        valuation * 0.30
        + quality * 0.25
        + growth * 0.20
        + trend * 0.15
        + portfolio_fit * 0.10,
        1,
    )

    severe_risk = any("severely" in flag.lower() or "materially above" in flag.lower() for flag in risk_flags)
    owned = bool(holding and holding.shares > 0)

    if owned and portfolio_fit <= 25:
        action = "TRIM"
    elif severe_risk and owned:
        action = "SELL_REVIEW"
    elif owned and score >= 45:
        action = "HOLD"
    elif not owned and score >= 72 and not severe_risk:
        action = "BUY"
    elif score < 35 and owned:
        action = "SELL_REVIEW"
    else:
        action = "WATCH"

    confidence = "high" if len(prices) >= 200 and fundamentals else "medium" if fundamentals else "low"
    return SignalDecision(
        date=latest_date,
        action=action,
        score=score,
        confidence=confidence,
        reasons=reasons[:8],
        risk_flags=risk_flags[:8],
    )


def encode_list(values: list[str]) -> str:
    return json.dumps(values)


def decode_list(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
