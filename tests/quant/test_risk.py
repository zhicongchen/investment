from __future__ import annotations

from dataclasses import replace

import pandas as pd

from quant.config import QuantConfig, RiskConfig
from quant.risk.controls import RiskState, high_volatility_block, record_trade_result, update_daily_state


def test_daily_loss_limit_halts_trading():
    config = replace(QuantConfig(watchlist=("AAA",)), risk=RiskConfig(daily_loss_limit=0.01))
    state = RiskState(current_day=pd.Timestamp("2024-01-02"), day_start_equity=1.0)

    update_daily_state(state, pd.Timestamp("2024-01-02 10:00"), 0.989, config)

    assert state.trading_halted is True
    assert state.reason == "daily_loss_limit"


def test_consecutive_loss_limit_halts_trading():
    config = replace(QuantConfig(watchlist=("AAA",)), risk=RiskConfig(consecutive_loss_limit=3))
    state = RiskState()

    for _ in range(3):
        record_trade_result(state, -0.001, config)

    assert state.trading_halted is True
    assert state.reason == "consecutive_loss_limit"


def test_high_volatility_block_uses_history_quantile():
    config = replace(QuantConfig(watchlist=("AAA",)), risk=RiskConfig(high_vol_quantile=0.90))
    current = pd.DataFrame({"rolling_vol_60m": [10.0]})
    history = pd.DataFrame({"rolling_vol_60m": [1.0, 2.0, 3.0]})

    assert high_volatility_block(current, history, config) is True
