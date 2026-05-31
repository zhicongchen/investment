from __future__ import annotations

from datetime import datetime, timedelta

from bot.config import BotConfig
from bot.strategy import ConservativeDipBuyStrategy
from bot.types import AccountSnapshot, DailyBar, Position, StrategyContext


def config(tmp_path, **kwargs) -> BotConfig:
    defaults = {
        "db_path": tmp_path / "bot.db",
        "tickers": ("SPY", "AAPL"),
        "core_etfs": ("SPY", "VOO", "QQQ"),
        "dip_buy_notional_usd": 100,
        "max_position_pct": 0.20,
        "trend_ma_days": 200,
        "dip_lookback_days": 20,
        "dip_min_pullback_pct": 0.03,
        "limit_discount_pct": 0.0025,
    }
    defaults.update(kwargs)
    return BotConfig(**defaults)


def bars(values: list[float]) -> list[DailyBar]:
    start = datetime(2025, 1, 1)
    return [DailyBar(date=start + timedelta(days=index), close=value) for index, value in enumerate(values)]


def context(cfg: BotConfig, history: dict[str, list[DailyBar]], positions=None, account=None) -> StrategyContext:
    return StrategyContext(
        account=account or AccountSnapshot(account="DU123", net_liquidation=10_000),
        positions=positions or {},
        market_data={},
        daily_history=history,
        now=datetime(2026, 6, 1, 10, 0),
        config=cfg,
    )


def qualifying_history() -> list[DailyBar]:
    values = [100 + index * 0.1 for index in range(180)]
    values += [118, 119, 120, 121, 122, 123, 124, 125, 126, 127]
    values += [128, 129, 130, 131, 132, 133, 134, 135, 132, 130]
    return bars(values)


def test_no_intent_without_enough_history(tmp_path):
    cfg = config(tmp_path)

    intents = ConservativeDipBuyStrategy().generate(context(cfg, {"SPY": bars([100] * 50)}))

    assert intents == []


def test_no_intent_when_below_long_term_trend(tmp_path):
    cfg = config(tmp_path)
    history = bars([120] * 199 + [90])

    intents = ConservativeDipBuyStrategy().generate(context(cfg, {"SPY": history}))

    assert intents == []


def test_no_intent_without_required_pullback(tmp_path):
    cfg = config(tmp_path)
    history = bars([100 + index * 0.1 for index in range(200)])

    intents = ConservativeDipBuyStrategy().generate(context(cfg, {"SPY": history}))

    assert intents == []


def test_no_intent_when_position_exceeds_max_allocation(tmp_path):
    cfg = config(tmp_path)
    positions = {"SPY": Position(account="DU123", ticker="SPY", quantity=30, market_value=3000)}

    intents = ConservativeDipBuyStrategy().generate(context(cfg, {"SPY": qualifying_history()}, positions=positions))

    assert intents == []


def test_emits_small_limit_buy_when_conditions_pass(tmp_path):
    cfg = config(tmp_path)

    intents = ConservativeDipBuyStrategy().generate(context(cfg, {"SPY": qualifying_history()}))

    assert len(intents) == 1
    intent = intents[0]
    assert intent.ticker == "SPY"
    assert intent.side == "BUY"
    assert intent.order_type == "LMT"
    assert intent.limit_price == 129.68
    assert intent.quantity == 0.7692
    assert "conservative dip buy" in intent.reason


def test_supports_etf_core_plus_stock_tickers(tmp_path):
    cfg = config(tmp_path)

    intents = ConservativeDipBuyStrategy().generate(
        context(cfg, {"SPY": qualifying_history(), "AAPL": qualifying_history()})
    )

    assert [intent.ticker for intent in intents] == ["SPY", "AAPL"]
