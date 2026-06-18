from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.config import QuantConfig


@dataclass
class RiskState:
    current_day: pd.Timestamp | None = None
    day_start_equity: float = 1.0
    consecutive_losses: int = 0
    trading_halted: bool = False
    reason: str = "ok"


def update_daily_state(state: RiskState, timestamp: pd.Timestamp, equity: float, config: QuantConfig) -> RiskState:
    day = pd.Timestamp(timestamp).normalize()
    if state.current_day is None or day != state.current_day:
        state.current_day = day
        state.day_start_equity = equity
        state.consecutive_losses = 0
        state.trading_halted = False
        state.reason = "ok"
    daily_return = equity / state.day_start_equity - 1
    if daily_return <= -config.risk.daily_loss_limit:
        state.trading_halted = True
        state.reason = "daily_loss_limit"
    if state.consecutive_losses >= config.risk.consecutive_loss_limit:
        state.trading_halted = True
        state.reason = "consecutive_loss_limit"
    return state


def record_trade_result(state: RiskState, trade_return: float, config: QuantConfig) -> RiskState:
    if trade_return < 0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0
    if state.consecutive_losses >= config.risk.consecutive_loss_limit:
        state.trading_halted = True
        state.reason = "consecutive_loss_limit"
    return state


def high_volatility_block(features_at_time: pd.DataFrame, history: pd.DataFrame, config: QuantConfig) -> bool:
    if "rolling_vol_60m" not in features_at_time or "rolling_vol_60m" not in history:
        return False
    threshold = history["rolling_vol_60m"].dropna().quantile(config.risk.high_vol_quantile)
    if pd.isna(threshold):
        return False
    return bool((features_at_time["rolling_vol_60m"] > threshold).any())
