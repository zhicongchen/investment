from __future__ import annotations

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from bot.config import BotConfig
from bot.storage import BotStorage
from bot.types import AccountSnapshot, BrokerStatus, ContractSummary, OrderIntent, Position, RiskResult


US_EASTERN = ZoneInfo("US/Eastern")
SUPPORTED_TIFS = {"DAY", "GTC"}


def evaluate_risk(
    *,
    intent: OrderIntent,
    config: BotConfig,
    storage: BotStorage,
    status: BrokerStatus,
    account: AccountSnapshot | None,
    positions: dict[str, Position],
    contract: ContractSummary | None,
    contract_error: str | None,
    now: datetime | None = None,
) -> RiskResult:
    current = now or datetime.now(US_EASTERN)
    reasons: list[str] = []
    ticker = intent.ticker.upper()
    side = intent.side.upper()
    order_type = intent.order_type.upper()

    if not config.live_trading_enabled:
        reasons.append("BOT_LIVE_TRADING_ENABLED is false")
    if not status.connected:
        reasons.append("TWS is not connected")
    if account is None or not account.account:
        reasons.append("IBKR account cannot be identified")
    if contract_error:
        reasons.append(contract_error)
    if contract and (contract.sec_type != "STK" or contract.exchange != "SMART" or contract.currency != "USD"):
        reasons.append("Only SMART-routed USD stock/ETF contracts are supported")
    if ticker not in config.tickers:
        reasons.append("Ticker is not in BOT_TICKERS")
    if not ticker.isalpha() or len(ticker) > 8:
        reasons.append("Only simple US stock/ETF symbols are supported")
    if side not in {"BUY", "SELL"}:
        reasons.append("Only BUY and SELL are supported")
    if order_type != "LMT":
        reasons.append("Only limit orders are supported")
    if intent.time_in_force.upper() not in SUPPORTED_TIFS:
        reasons.append("Only DAY and GTC time in force values are supported")
    if intent.quantity <= 0:
        reasons.append("Quantity must be greater than zero")
    if intent.limit_price <= 0:
        reasons.append("Limit price must be greater than zero")
    if intent.estimated_notional > config.max_order_notional_usd:
        reasons.append(f"Order notional exceeds ${config.max_order_notional_usd:,.0f}")
    if not config.allow_after_hours and not regular_market_hours(current):
        reasons.append("Orders are blocked outside regular US market hours")
    daily_notional = storage.daily_submitted_notional(current.date().isoformat())
    if daily_notional + intent.estimated_notional > config.max_daily_notional_usd:
        reasons.append(f"Daily submitted notional would exceed ${config.max_daily_notional_usd:,.0f}")
    if side == "SELL" and not config.allow_shorts:
        position = positions.get(ticker)
        available = position.quantity if position else 0
        if intent.quantity > available:
            reasons.append("Sell quantity exceeds synced IBKR position")
    cooldown_since = (current - timedelta(seconds=config.duplicate_cooldown_seconds)).replace(tzinfo=None).isoformat(timespec="seconds")
    if storage.recent_submission_exists(ticker, side, cooldown_since):
        reasons.append("Duplicate order cooldown is active")
    return RiskResult(allowed=not reasons, reasons=reasons)


def regular_market_hours(value: datetime) -> bool:
    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=US_EASTERN)
    local = current.astimezone(US_EASTERN)
    if local.weekday() >= 5:
        return False
    return time(9, 30) <= local.time() <= time(16, 0)
