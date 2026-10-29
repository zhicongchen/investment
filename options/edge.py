"""Risk/reward analytics for a short put and a 0-100 put-selling score.

Premium yield alone rewards the riskiest contracts. A put is worth selling when:

1. The market overpays for it: implied volatility is above what the stock actually realizes
   (the variance risk premium), so the premium beats its fair value at realized volatility.
2. The odds favor keeping the premium: high probability of finishing above breakeven, with the
   strike at least about one expected move (one standard deviation) below spot.
3. No earnings release lands before expiry (gap risk the premium rarely pays for).
4. Assignment would buy a business you want, at a price below its estimated fair value.
5. Time to expiry sits near the usual 30-45 day sweet spot: annualized yield flatters weekly
   puts, which carry the most gamma risk (a sharp drop moves them against you fastest).

Pure functions over PutCandidate; the scanner supplies realized volatility, the next earnings
date and the fundamental verdict from the dashboard's financial analysis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from options.config import EdgeConfig
from options.pricing import put_price
from options.puts import PutCandidate

AVOID_VERDICTS = {"AVOID", "SELL_REVIEW"}
OWNABLE_VERDICTS = {"BUY", "ADD", "ACCUMULATE", "HOLD"}


@dataclass(frozen=True)
class Underlying:
    """The dashboard's fundamental view of a stock (backend financial analysis)."""

    action: str
    conviction: str = "low"
    composite_score: Optional[float] = None
    fair_value: Optional[float] = None
    buy_below: Optional[float] = None


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def prob_above(spot: float, level: float, years: float, rate: float, vol: float) -> Optional[float]:
    """Risk-neutral probability that the stock finishes above `level` (lognormal, Black-Scholes N(d2))."""
    if spot <= 0 or level <= 0 or years <= 0 or vol <= 0:
        return None
    d2 = (math.log(spot / level) + (rate - 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    return _norm_cdf(d2)


def realized_vol(closes: Sequence[float], days: int = 30) -> Optional[float]:
    """Annualized close-to-close volatility over the last `days` trading days."""
    window = [value for value in closes[-(days + 1) :] if value and value > 0]
    if len(window) < min(days, 15) + 1:
        return None
    returns = [math.log(b / a) for a, b in zip(window, window[1:])]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _scale(value: Optional[float], points: Sequence[tuple[float, float]]) -> Optional[float]:
    """Piecewise-linear map onto 0-100 through (metric, score) anchor points."""
    if value is None:
        return None
    ordered = sorted(points)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return None


def evaluate(
    item: PutCandidate,
    config: EdgeConfig,
    rate: float,
    hv: Optional[float] = None,
    earnings: Optional[date] = None,
    underlying: Optional[Underlying] = None,
    is_dip: bool = False,
) -> PutCandidate:
    """Fill the edge fields and the 0-100 put score on a candidate (in place) and return it."""
    years = max(item.dte, 1) / 365.0
    vol = item.iv
    item.hv_30d = hv
    if vol:
        item.prob_otm = prob_above(item.spot, item.strike, years, rate, vol)
        item.prob_profit = prob_above(item.spot, item.breakeven, years, rate, vol)
        expected_move = item.spot * vol * math.sqrt(years)
        item.expected_move_pct = expected_move / item.spot
        item.strike_sd = (item.spot - item.strike) / expected_move if expected_move > 0 else None
    if hv:
        theoretical = put_price(item.spot, item.strike, years, rate, hv)
        item.edge = item.premium - theoretical
        item.edge_pct = item.edge / item.premium if item.premium > 0 else None
        item.contract_iv_hv = vol / hv if vol else None
    item.earnings_date = earnings
    item.earnings_in_trade = earnings is not None and earnings <= item.expiry
    if underlying is not None:
        item.verdict = underlying.action
        item.fair_value = underlying.fair_value
        item.buy_below = underlying.buy_below
        if underlying.fair_value:
            item.breakeven_discount = 1 - item.breakeven / underlying.fair_value

    reasons: list[str] = []
    cautions: list[str] = []
    parts: list[tuple[Optional[float], float]] = []

    parts.append((_scale(item.annualized_yield, [(0.05, 20), (0.10, 45), (0.15, 65), (0.25, 85), (0.40, 95)]), 0.20))

    richness = _scale(item.contract_iv_hv, [(0.8, 10), (1.0, 40), (1.2, 65), (1.5, 85), (2.0, 95)])
    if item.iv_rank is not None:
        rank_score = _scale(item.iv_rank, [(10, 20), (30, 45), (50, 70), (80, 95)])
        richness = rank_score if richness is None else (richness + rank_score) / 2
    parts.append((richness, 0.20))
    if item.contract_iv_hv is not None and item.iv is not None and hv:
        if item.contract_iv_hv >= 1.15:
            reasons.append(f"Premium is rich: IV {item.iv:.0%} vs {hv:.0%} realized ({item.contract_iv_hv:.1f}x)")
        elif item.contract_iv_hv < 0.95:
            cautions.append(f"IV {item.iv:.0%} is below realized volatility {hv:.0%}: the premium is cheap")

    parts.append((_scale(item.prob_profit, [(0.55, 5), (0.65, 30), (0.75, 60), (0.85, 85), (0.92, 95)]), 0.20))
    parts.append((_scale(item.strike_sd, [(0.3, 10), (0.7, 40), (1.0, 65), (1.5, 88), (2.0, 95)]), 0.10))
    if item.prob_profit is not None and item.strike_sd is not None:
        text = f"{item.prob_profit:.0%} chance of profit; strike is {item.strike_sd:.1f} expected moves below spot"
        (reasons if item.prob_profit >= 0.75 and item.strike_sd >= 0.8 else cautions).append(text)

    quality: Optional[float] = None
    if underlying is not None and underlying.action not in {"INSUFFICIENT_DATA"}:
        components = [
            score
            for score in (
                underlying.composite_score,
                _scale(item.breakeven_discount, [(-0.4, 5), (-0.15, 30), (0, 50), (0.15, 75), (0.3, 95)]),
            )
            if score is not None
        ]
        quality = sum(components) / len(components) if components else None
        if item.buy_below and item.strike <= item.buy_below:
            quality = min(100.0, (quality or 50) + 10)
            reasons.append(f"Assignment at ${item.strike:g} is below the ${item.buy_below:,.2f} buy-below price")
        elif item.fair_value and item.breakeven_discount is not None:
            relation = "below" if item.breakeven_discount >= 0 else "above"
            text = f"Breakeven ${item.breakeven:,.2f} is {abs(item.breakeven_discount):.0%} {relation} estimated fair value ${item.fair_value:,.2f}"
            (reasons if item.breakeven_discount >= 0 else cautions).append(text)
    else:
        cautions.append("No fundamental check: only sell puts on stocks you are happy to own at the strike")
    parts.append((quality, 0.25))

    parts.append((_scale(item.spread_pct, [(0.02, 95), (0.05, 70), (0.10, 40), (0.20, 10)]), 0.05))
    parts.append((_scale(item.dte, [(7, 30), (14, 55), (21, 75), (30, 95), (45, 95), (60, 80)]), 0.10))
    if item.dte < 14:
        cautions.append(f"Only {item.dte} days to expiry: high gamma risk, a sharp drop moves the put against you quickly")

    scored = [(score, weight) for score, weight in parts if score is not None]
    total = sum(score * weight for score, weight in scored) / sum(weight for _, weight in scored) if scored else 0.0
    if is_dip:
        total += 3
        reasons.append("Selling into a pullback")
    if item.earnings_in_trade:
        cautions.append(f"Earnings on {earnings:%Y-%m-%d} before expiry: gap risk")
        if config.earnings == "penalize":
            total -= 15

    grade = "A" if total >= 75 else "B" if total >= 62 else "C" if total >= 50 else "PASS"
    if underlying is None or underlying.action == "INSUFFICIENT_DATA" or item.earnings_in_trade:
        grade = {"A": "B"}.get(grade, grade)  # an unverified business or an earnings gap caps the grade at B
    if item.verdict in AVOID_VERDICTS:
        grade = "AVOID"
        cautions.insert(0, f"Fundamental verdict is {item.verdict}: assignment would buy a stock the analysis rejects")
    item.put_score = round(max(0.0, min(100.0, total)), 1)
    item.grade = grade
    item.reasons = reasons
    item.cautions = cautions
    return item


def excluded(item: PutCandidate, config: EdgeConfig) -> Optional[str]:
    """Rejection reason for a hard filter, or None to keep the candidate."""
    if config.exclude_fundamental_avoid and item.verdict in AVOID_VERDICTS:
        return "fundamentals: avoid"
    if config.earnings == "exclude" and item.earnings_in_trade:
        return "earnings before expiry"
    if config.min_prob_profit and item.prob_profit is not None and item.prob_profit < config.min_prob_profit:
        return "probability of profit too low"
    return None
