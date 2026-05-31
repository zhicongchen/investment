from __future__ import annotations

from datetime import datetime

from bot.config import BotConfig
from bot.risk import evaluate_risk
from bot.storage import BotStorage
from bot.types import AccountSnapshot, BrokerStatus, ContractSummary, OrderIntent, Position, SubmitResult


MARKET_OPEN = datetime(2026, 6, 1, 10, 0)
AFTER_HOURS = datetime(2026, 6, 1, 20, 0)


def config(tmp_path, **kwargs) -> BotConfig:
    defaults = {
        "db_path": tmp_path / "bot.db",
        "live_trading_enabled": True,
        "tickers": ("AAPL", "MSFT", "SPY"),
        "max_order_notional_usd": 500,
        "max_daily_notional_usd": 2000,
        "allow_after_hours": False,
        "allow_shorts": False,
    }
    defaults.update(kwargs)
    return BotConfig(**defaults)


def intent(**kwargs) -> OrderIntent:
    values = {
        "ticker": "AAPL",
        "side": "BUY",
        "quantity": 1,
        "order_type": "LMT",
        "limit_price": 100,
        "reason": "test",
    }
    values.update(kwargs)
    return OrderIntent(**values)


def allowed_context(tmp_path, **config_overrides):
    cfg = config(tmp_path, **config_overrides)
    storage = BotStorage(cfg.db_path)
    return {
        "config": cfg,
        "storage": storage,
        "status": BrokerStatus(connected=True),
        "account": AccountSnapshot(account="DU123"),
        "positions": {"AAPL": Position(account="DU123", ticker="AAPL", quantity=5)},
        "contract": ContractSummary(symbol="AAPL"),
        "contract_error": None,
        "now": MARKET_OPEN,
    }


def test_blocks_live_trading_disabled(tmp_path):
    context = allowed_context(tmp_path, live_trading_enabled=False)
    result = evaluate_risk(intent=intent(), **context)

    assert result.allowed is False
    assert "BOT_LIVE_TRADING_ENABLED is false" in result.reasons


def test_blocks_market_orders(tmp_path):
    result = evaluate_risk(intent=intent(order_type="MKT"), **allowed_context(tmp_path))

    assert result.allowed is False
    assert "Only limit orders are supported" in result.reasons


def test_blocks_oversized_orders(tmp_path):
    result = evaluate_risk(intent=intent(quantity=10, limit_price=100), **allowed_context(tmp_path))

    assert result.allowed is False
    assert "Order notional exceeds $500" in result.reasons


def test_blocks_after_hours(tmp_path):
    context = allowed_context(tmp_path)
    context["now"] = AFTER_HOURS

    result = evaluate_risk(intent=intent(), **context)

    assert result.allowed is False
    assert "Orders are blocked outside regular US market hours" in result.reasons


def test_blocks_shorts(tmp_path):
    result = evaluate_risk(intent=intent(side="SELL", quantity=10), **allowed_context(tmp_path))

    assert result.allowed is False
    assert "Sell quantity exceeds synced IBKR position" in result.reasons


def test_blocks_duplicate_cooldown(tmp_path):
    context = allowed_context(tmp_path)
    decision_id = context["storage"].record_decision(intent())
    context["storage"].record_submission(
        decision_id,
        intent(),
        SubmitResult(order_id=1, perm_id=2, status="Submitted", remaining=1),
    )
    context["storage"].conn.execute(
        "update submitted_orders set created_at = ?",
        (MARKET_OPEN.replace(microsecond=0).isoformat(),),
    )
    context["storage"].conn.commit()

    result = evaluate_risk(intent=intent(), **context)

    assert result.allowed is False
    assert "Duplicate order cooldown is active" in result.reasons


def test_allows_valid_guarded_limit_order(tmp_path):
    result = evaluate_risk(intent=intent(), **allowed_context(tmp_path))

    assert result.allowed is True
    assert result.reasons == []
