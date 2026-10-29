"""Select and rank short-put candidates. Pure functions over normalized option quotes."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from options.config import PutsConfig, RankingConfig, SizingConfig
from options.pricing import implied_vol, put_delta
from shared.moomoo import OptionQuote, UnderlyingVol

CONTRACT_MULTIPLIER = 100


@dataclass
class PutCandidate:
    ticker: str
    code: str
    expiry: date
    dte: int
    strike: float
    spot: float
    bid: float
    ask: float
    premium: float
    spread_pct: float
    delta: Optional[float]
    delta_estimated: bool
    iv: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]
    otm_pct: float
    annualized_yield: float
    breakeven: float
    cushion_pct: float
    collateral: float
    iv_rank: Optional[float] = None
    iv_hv_ratio: Optional[float] = None
    contracts: Optional[int] = None
    score: float = 0.0
    # Filled by options.edge.evaluate
    hv_30d: Optional[float] = None
    contract_iv_hv: Optional[float] = None
    prob_otm: Optional[float] = None
    prob_profit: Optional[float] = None
    expected_move_pct: Optional[float] = None
    strike_sd: Optional[float] = None
    edge: Optional[float] = None
    edge_pct: Optional[float] = None
    earnings_date: Optional[date] = None
    earnings_in_trade: bool = False
    verdict: Optional[str] = None
    fair_value: Optional[float] = None
    buy_below: Optional[float] = None
    breakeven_discount: Optional[float] = None
    put_score: Optional[float] = None
    grade: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)


def select_puts(
    spot: float,
    quotes: list[OptionQuote],
    config: PutsConfig,
    today: date,
    vol: Optional[UnderlyingVol] = None,
    sizing: Optional[SizingConfig] = None,
) -> tuple[list[PutCandidate], Counter[str]]:
    """Filter quotes down to sellable puts. Returns candidates and a count of rejection reasons."""
    candidates: list[PutCandidate] = []
    rejected: Counter[str] = Counter()
    vol = vol or UnderlyingVol()
    for quote in quotes:
        dte = (quote.expiry - today).days
        if dte < config.min_dte or dte > config.max_dte:
            rejected["dte out of range"] += 1
            continue
        if spot <= 0 or quote.strike >= spot:
            rejected["not out of the money"] += 1
            continue
        otm_pct = 1.0 - quote.strike / spot
        if otm_pct < config.min_otm_pct or otm_pct > config.max_otm_pct:
            rejected["otm % out of range"] += 1
            continue
        if quote.bid is None or quote.ask is None or quote.bid < config.min_bid or quote.ask < quote.bid:
            rejected["bid too low / no market"] += 1
            continue
        mid = (quote.bid + quote.ask) / 2
        spread_pct = (quote.ask - quote.bid) / mid if mid > 0 else math.inf
        if spread_pct > config.max_spread_pct:
            rejected["spread too wide"] += 1
            continue
        if quote.open_interest is not None and quote.open_interest < config.min_open_interest:
            rejected["open interest too low"] += 1
            continue

        years = max(dte, 1) / 365.0
        iv = quote.iv if quote.iv else implied_vol(mid, spot, quote.strike, years, config.risk_free_rate)
        delta = quote.delta
        delta_estimated = False
        if delta is None and iv:
            delta = put_delta(spot, quote.strike, years, config.risk_free_rate, iv)
            delta_estimated = delta is not None
        if delta is None:
            rejected["no delta"] += 1
            continue
        if delta < config.delta_min or delta > config.delta_max:
            rejected["delta out of range"] += 1
            continue

        premium = mid if config.price_basis == "mid" else quote.bid
        collateral = quote.strike * CONTRACT_MULTIPLIER
        breakeven = quote.strike - premium
        contracts = None
        if sizing is not None and sizing.max_collateral_per_trade:
            contracts = int(sizing.max_collateral_per_trade // collateral)
        candidates.append(
            PutCandidate(
                ticker=quote.ticker,
                code=quote.code,
                expiry=quote.expiry,
                dte=dte,
                strike=quote.strike,
                spot=spot,
                bid=quote.bid,
                ask=quote.ask,
                premium=round(premium, 4),
                spread_pct=spread_pct,
                delta=delta,
                delta_estimated=delta_estimated,
                iv=iv,
                open_interest=quote.open_interest,
                volume=quote.volume,
                otm_pct=otm_pct,
                annualized_yield=premium / quote.strike * 365.0 / max(dte, 1),
                breakeven=breakeven,
                cushion_pct=(spot - breakeven) / spot,
                collateral=collateral,
                iv_rank=vol.iv_rank,
                iv_hv_ratio=vol.iv_hv_ratio,
                contracts=contracts,
            )
        )
    return candidates, rejected


def rank_candidates(
    candidates: list[PutCandidate], ranking: RankingConfig, top_per_ticker: int
) -> list[PutCandidate]:
    """Score, sort and trim candidates. Higher score = richer put to sell."""
    if not candidates:
        return []
    metric = ranking.metric
    if metric == "blend":
        _blend_scores(candidates, ranking.blend_weights)
        key = lambda item: (item.score, item.annualized_yield)  # noqa: E731
    elif metric == "annualized_yield":
        for item in candidates:
            item.score = item.annualized_yield
        key = lambda item: item.score  # noqa: E731
    else:
        for item in candidates:
            value = getattr(item, metric)
            item.score = value if value is not None else float("-inf")
        key = lambda item: (item.score, item.annualized_yield)  # noqa: E731

    ranked = sorted(candidates, key=key, reverse=True)
    per_ticker: Counter[str] = Counter()
    kept: list[PutCandidate] = []
    for item in ranked:
        if per_ticker[item.ticker] >= top_per_ticker:
            continue
        per_ticker[item.ticker] += 1
        kept.append(item)
    return kept[: ranking.top_n] if ranking.top_n else kept


def _blend_scores(candidates: list[PutCandidate], weights: dict[str, float]) -> None:
    """Weighted average of percentile ranks (0-100) within this candidate set; missing metrics are skipped."""
    percentiles: dict[str, dict[int, float]] = {}
    for metric in weights:
        values = [(index, getattr(item, metric, None)) for index, item in enumerate(candidates)]
        present = [(index, value) for index, value in values if value is not None]
        if not present:
            continue
        ordered = sorted(value for _, value in present)
        percentiles[metric] = {
            index: 100.0 * _percentile_rank(ordered, value) for index, value in present
        }
    for index, item in enumerate(candidates):
        total = 0.0
        weight_sum = 0.0
        for metric, weight in weights.items():
            value = percentiles.get(metric, {}).get(index)
            if value is None or weight <= 0:
                continue
            total += weight * value
            weight_sum += weight
        item.score = total / weight_sum if weight_sum else 0.0


def _percentile_rank(ordered: list[float], value: float) -> float:
    if len(ordered) == 1:
        return 1.0
    below = sum(1 for item in ordered if item < value)
    equal = sum(1 for item in ordered if item == value)
    return (below + (equal - 1) / 2) / (len(ordered) - 1)
