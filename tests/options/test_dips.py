from __future__ import annotations

from datetime import date, timedelta

from options.config import DipConfig
from options.dips import evaluate_dip, rsi
from shared.moomoo import DailyBar


def bars_from(closes: list[float]) -> list[DailyBar]:
    start = date(2026, 1, 1)
    return [DailyBar(date=start + timedelta(days=index), high=close, close=close) for index, close in enumerate(closes)]


def test_off_high_triggers_dip():
    closes = [100.0] * 25 + [88.0]
    metrics = evaluate_dip("ABC", bars_from(closes), DipConfig())
    assert metrics.is_dip
    assert abs(metrics.off_high_pct - 0.12) < 1e-9
    assert any("below 20d high" in trigger for trigger in metrics.triggers)


def test_flat_stock_is_not_a_dip():
    metrics = evaluate_dip("ABC", bars_from([100.0] * 30), DipConfig())
    assert not metrics.is_dip
    assert metrics.triggers == []


def test_rule_all_requires_every_enabled_check():
    config = DipConfig(rule="all")
    # 12% off the 20d high but the 5-day return is only -2%.
    closes = [100.0] * 15 + [90.0, 90.0, 90.0, 90.0, 89.8, 88.0]
    metrics = evaluate_dip("ABC", bars_from(closes), config)
    assert metrics.off_high_pct >= 0.10
    assert metrics.n_day_return_pct > -0.05
    assert not metrics.is_dip


def test_trend_filter_blocks_falling_knife():
    config = DipConfig()
    config.require_above_ma.enabled = True
    config.require_above_ma.days = 30
    closes = [150.0] * 30 + [100.0] * 20 + [85.0]
    metrics = evaluate_dip("ABC", bars_from(closes), config)
    assert not metrics.is_dip
    assert any("below 30d MA" in reason for reason in metrics.blocked_by)


def test_rsi_bounds():
    assert rsi([float(value) for value in range(1, 30)]) == 100.0
    falling = rsi([float(value) for value in range(30, 1, -1)])
    assert falling is not None and falling < 1
    assert rsi([1.0, 2.0], period=14) is None
    assert rsi([100.0] * 30) == 50.0
