from __future__ import annotations

import math
from typing import Protocol

from bot.types import DailyBar, OrderIntent, StrategyContext


class Strategy(Protocol):
    name: str

    def generate(self, context: StrategyContext) -> list[OrderIntent]:
        ...


class NoOpStrategy:
    name = "noop"

    def generate(self, context: StrategyContext) -> list[OrderIntent]:
        return []


class ExampleHookStrategy:
    name = "example_hook"

    def generate(self, context: StrategyContext) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        for ticker, snapshot in context.market_data.items():
            if snapshot.last_price is None:
                continue
            # Example hook only demonstrates the interface. Keep size tiny and let risk checks decide.
            intents.append(
                OrderIntent(
                    ticker=ticker,
                    side="BUY",
                    quantity=1,
                    order_type="LMT",
                    limit_price=round(snapshot.last_price * 0.99, 2),
                    reason="example hook: bid 1% below last price",
                )
            )
        return intents


class ConservativeDipBuyStrategy:
    name = "conservative_dip"

    def generate(self, context: StrategyContext) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        config = context.config
        tickers = tuple(getattr(config, "tickers", ()))
        core_etfs = set(getattr(config, "core_etfs", ()))
        ordered_tickers = sorted(tickers, key=lambda ticker: (ticker not in core_etfs, ticker))
        for ticker in ordered_tickers:
            bars = context.daily_history.get(ticker, [])
            if not _has_enough_history(bars, config):
                continue
            latest_close = bars[-1].close
            trend_ma = _average_close(bars[-config.trend_ma_days :])
            lookback_high = max(bar.close for bar in bars[-config.dip_lookback_days :])
            pullback = (lookback_high - latest_close) / lookback_high if lookback_high > 0 else 0
            if latest_close <= trend_ma:
                continue
            if pullback < config.dip_min_pullback_pct:
                continue
            if _position_pct(context, ticker, latest_close) >= config.max_position_pct:
                continue
            quantity = math.floor((config.dip_buy_notional_usd / latest_close) * 10000) / 10000
            if quantity <= 0:
                continue
            limit_price = round(latest_close * (1 - config.limit_discount_pct), 2)
            intents.append(
                OrderIntent(
                    ticker=ticker,
                    side="BUY",
                    quantity=quantity,
                    order_type="LMT",
                    limit_price=limit_price,
                    reason=(
                        f"conservative dip buy: close above {config.trend_ma_days}d MA, "
                        f"{pullback:.1%} below {config.dip_lookback_days}d high"
                    ),
                )
            )
        return intents


def _has_enough_history(bars: list[DailyBar], config: object) -> bool:
    return len(bars) >= max(config.trend_ma_days, config.dip_lookback_days)


def _average_close(bars: list[DailyBar]) -> float:
    return sum(bar.close for bar in bars) / len(bars)


def _position_pct(context: StrategyContext, ticker: str, latest_close: float) -> float:
    account = context.account
    if account is None or not account.net_liquidation:
        return 1.0
    position = context.positions.get(ticker)
    if position is None:
        return 0.0
    market_value = position.market_value
    if market_value is None:
        market_value = position.quantity * latest_close
    return market_value / account.net_liquidation
