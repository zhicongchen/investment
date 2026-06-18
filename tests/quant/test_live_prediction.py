from __future__ import annotations

from dataclasses import replace

import pandas as pd

from quant.config import CostConfig, QuantConfig
from quant.live import cost_adjustment, latest_completed_bar_time


def test_latest_completed_bar_excludes_current_partial_minute():
    now = pd.Timestamp("2026-06-18 10:30:25", tz="America/New_York")

    assert latest_completed_bar_time(now) == pd.Timestamp("2026-06-18 10:29:00", tz="America/New_York")


def test_cost_adjustment_uses_round_trip_spread_and_buffer(tmp_path):
    config = replace(
        QuantConfig(watchlist=("AAA",), data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"),
        costs=CostConfig(commission_rate=0.0001, slippage_rate=0.0003, spread_estimate=0.0002, safety_buffer=0.0005),
    )

    result = cost_adjustment(0.01, config)

    assert result["estimated_round_trip_cost"] == 0.001
    assert result["net_expected_return"] == 0.0085
