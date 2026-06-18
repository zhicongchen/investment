from __future__ import annotations

from dataclasses import replace

import pandas as pd

from quant.backtest.engine import run_backtest
from quant.config import QuantConfig


def bars() -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-02 09:30", periods=70, freq="min")
    rows = []
    for ticker in ["AAA", "QQQ", "SPY"]:
        for idx, timestamp in enumerate(timestamps):
            price = 100 + idx
            rows.append({"ticker": ticker, "timestamp": timestamp, "open": price + 0.1, "high": price + 1, "low": price - 1, "close": price, "volume": 1000})
    return pd.DataFrame(rows)


def test_backtest_executes_signal_at_next_minute_open_and_charges_costs(tmp_path):
    config = replace(
        QuantConfig(watchlist=("AAA",), data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"),
        portfolio=replace(QuantConfig(watchlist=("AAA",)).portfolio, max_positions=1, max_position_size=0.5),
    )
    signals = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2024-01-02 09:30"), pd.Timestamp("2024-01-02 09:31")],
            "ticker": ["AAA", "AAA"],
            "probability_up": [0.8, 0.0],
            "probability_down": [0.1, 0.1],
            "probability_flat": [0.1, 0.9],
            "predicted_class": [1, 0],
            "signal": ["LONG", "NO_TRADE"],
        }
    )
    features = bars()[["ticker", "timestamp"]].assign(rolling_vol_60m=0.0)

    result = run_backtest(bars(), features, signals, config)

    trade = result["trades"].iloc[0]
    assert trade["entry_timestamp"] == pd.Timestamp("2024-01-02 09:31")
    assert trade["entry_price"] > 101.1
    assert trade["trade_return"] < ((101 / 101.1) - 1) * 0.5

