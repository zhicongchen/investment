"""Detect stocks that are going down. Pure functions over daily bars."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from options.config import DipConfig
from shared.moomoo import DailyBar


@dataclass
class DipMetrics:
    ticker: str
    spot: float
    day_change_pct: Optional[float] = None
    off_high_pct: Optional[float] = None
    n_day_return_pct: Optional[float] = None
    rsi: Optional[float] = None
    moving_average: Optional[float] = None
    triggers: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    is_dip: bool = False


def evaluate_dip(ticker: str, bars: list[DailyBar], config: DipConfig, prev_close: Optional[float] = None) -> DipMetrics:
    """Compute drawdown metrics for bars whose last row is the latest (possibly live) price."""
    if not bars:
        return DipMetrics(ticker=ticker, spot=0.0, blocked_by=["no price history"])
    closes = [bar.close for bar in bars]
    spot = closes[-1]
    checks = config.checks
    reference_close = prev_close if prev_close else (closes[-2] if len(closes) >= 2 else None)

    metrics = DipMetrics(
        ticker=ticker,
        spot=spot,
        day_change_pct=_pct_change(reference_close, spot),
        off_high_pct=_off_high(bars, checks.off_high.lookback_days),
        n_day_return_pct=_pct_change(closes[-1 - checks.drop_n_days.days], spot)
        if len(closes) > checks.drop_n_days.days
        else None,
        rsi=rsi(closes, checks.rsi.period),
        moving_average=_mean(closes[-config.require_above_ma.days :]) if len(closes) >= config.require_above_ma.days else None,
    )

    results: list[bool] = []
    if checks.off_high.enabled:
        hit = metrics.off_high_pct is not None and metrics.off_high_pct >= checks.off_high.min_pct
        results.append(hit)
        if hit:
            metrics.triggers.append(f"{metrics.off_high_pct:.1%} below {checks.off_high.lookback_days}d high")
    if checks.drop_n_days.enabled:
        hit = metrics.n_day_return_pct is not None and metrics.n_day_return_pct <= -checks.drop_n_days.min_pct
        results.append(hit)
        if hit:
            metrics.triggers.append(f"{metrics.n_day_return_pct:+.1%} over {checks.drop_n_days.days}d")
    if checks.rsi.enabled:
        hit = metrics.rsi is not None and metrics.rsi <= checks.rsi.max
        results.append(hit)
        if hit:
            metrics.triggers.append(f"RSI({checks.rsi.period}) {metrics.rsi:.0f}")
    if checks.day_drop.enabled:
        hit = metrics.day_change_pct is not None and metrics.day_change_pct <= -checks.day_drop.min_pct
        results.append(hit)
        if hit:
            metrics.triggers.append(f"{metrics.day_change_pct:+.1%} today")

    if not results:
        passed = False
        metrics.blocked_by.append("no dip checks enabled")
    elif config.rule == "all":
        passed = all(results)
    else:
        passed = any(results)

    if passed and config.require_above_ma.enabled:
        if metrics.moving_average is None:
            passed = False
            metrics.blocked_by.append(f"not enough history for {config.require_above_ma.days}d MA")
        elif spot < metrics.moving_average:
            passed = False
            metrics.blocked_by.append(f"below {config.require_above_ma.days}d MA {metrics.moving_average:.2f}")

    metrics.is_dip = passed
    return metrics


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI."""
    if period <= 0 or len(closes) <= period:
        return None
    deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _off_high(bars: list[DailyBar], lookback_days: int) -> Optional[float]:
    if lookback_days <= 0 or not bars:
        return None
    window = bars[-lookback_days:]
    high = max(max(bar.high, bar.close) for bar in window)
    if high <= 0:
        return None
    return (high - bars[-1].close) / high


def _pct_change(start: Optional[float], end: float) -> Optional[float]:
    if not start:
        return None
    return end / start - 1.0


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None
