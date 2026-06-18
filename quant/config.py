from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


DEFAULT_FEATURE_COLUMNS = (
    "ret_1m",
    "return_1m",
    "return_2m",
    "return_3m",
    "return_5m",
    "return_10m",
    "return_15m",
    "return_30m",
    "return_60m",
    "ret_3m",
    "ret_5m",
    "ret_10m",
    "ret_15m",
    "ret_30m",
    "ret_60m",
    "log_ret_1m",
    "log_return_1m",
    "cumulative_return_from_open",
    "return_since_open",
    "ma_5_gap",
    "ma_15_gap",
    "ma_30_gap",
    "ma_60_gap",
    "ema_5_gap",
    "ema_5",
    "ema_10",
    "ema_20",
    "ema_30",
    "ema_60",
    "price_vs_ema_5",
    "price_vs_ema_20",
    "price_vs_ema_60",
    "ema_5_vs_ema_20",
    "ema_20_vs_ema_60",
    "rolling_zscore_price_20",
    "rolling_zscore_price_60",
    "ema_15_gap",
    "ema_30_gap",
    "price_vs_vwap",
    "rolling_vol_5m",
    "realized_vol_5",
    "realized_vol_15",
    "realized_vol_30",
    "realized_vol_60",
    "rolling_high_low_range_5",
    "rolling_high_low_range_15",
    "rolling_high_low_range_60",
    "rolling_vol_15m",
    "rolling_vol_30m",
    "rolling_vol_60m",
    "ATR_14",
    "high_low_range",
    "current_bar_range",
    "current_bar_body",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "distance_to_intraday_high",
    "distance_to_intraday_low",
    "intraday_range_position",
    "close_position",
    "volume_ratio_5m",
    "volume_ratio_20m",
    "volume_ratio_60m",
    "volume_zscore_20m",
    "volume_change_1m",
    "volume_ma_5",
    "volume_ma_20",
    "volume_zscore_60",
    "dollar_volume",
    "rolling_dollar_volume_20m",
    "dollar_volume_zscore",
    "relative_volume_same_minute",
    "vwap_intraday",
    "RSI_14",
    "MACD",
    "MACD_signal",
    "MACD_histogram",
    "minute_of_day",
    "minute_of_day_sin",
    "minute_of_day_cos",
    "minutes_from_open",
    "minutes_to_close",
    "is_first_15m",
    "is_opening_15m",
    "is_opening_30m",
    "is_first_30m",
    "is_lunch_period",
    "is_last_30m",
    "is_closing_30m",
    "is_closing_15m",
    "hour",
    "minute",
    "qqq_ret_5m",
    "qqq_return_1m",
    "qqq_return_5m",
    "qqq_return_15m",
    "qqq_return_30m",
    "qqq_return_60m",
    "qqq_ret_15m",
    "qqq_ret_60m",
    "qqq_vol_30m",
    "spy_ret_5m",
    "spy_return_1m",
    "spy_return_5m",
    "spy_return_15m",
    "spy_return_30m",
    "spy_return_60m",
    "spy_ret_15m",
    "spy_ret_60m",
    "spy_vol_30m",
    "ticker_ret_5m_minus_qqq",
    "relative_return_vs_qqq_1m",
    "relative_return_vs_qqq_5m",
    "relative_return_vs_qqq_15m",
    "relative_return_vs_qqq_60m",
    "rolling_beta_vs_qqq_60",
    "rolling_corr_vs_qqq_60",
    "cross_section_mean_return_1m",
    "cross_section_median_return_1m",
    "cross_section_std_return_1m",
    "cross_section_mean_return_5m",
    "cross_section_rank_return_5m",
    "cross_section_rank_return_15m",
    "cross_section_rank_volume_zscore",
    "advance_ratio",
    "ticker_ret_15m_minus_qqq",
    "ticker_ret_60m_minus_qqq",
    "ticker_ret_15m_minus_spy",
    "ticker_code",
)


@dataclass(frozen=True)
class DataConfig:
    period: str = "30d"
    interval: str = "1m"
    request_delay_seconds: float = 2.0
    request_delay_jitter_seconds: float = 2.0


@dataclass(frozen=True)
class LabelConfig:
    horizon_minutes: int = 60
    cost_threshold: float = 0.0015


@dataclass(frozen=True)
class ModelConfig:
    probability_threshold: float = 0.55
    train_days: int = 10
    validation_days: int = 5
    test_days: int = 5


@dataclass(frozen=True)
class ValidationConfig:
    mode: str = "purged_walk_forward"
    window_type: str = "expanding"
    train_min_days: int = 3
    validation_days: int = 1
    test_days: int = 1
    purge_minutes: int = 60
    embargo_minutes: int = 60
    dynamic_cost_floor: float = 0.0005
    dynamic_k_values: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class PortfolioConfig:
    max_positions: int = 3
    max_position_size: float = 0.30
    initial_cash: float = 1.0


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.0001
    slippage_rate: float = 0.0003
    spread_estimate: float = 0.0002
    safety_buffer: float = 0.0005

    @property
    def total_rate(self) -> float:
        return self.commission_rate + self.slippage_rate

    @property
    def round_trip_cost(self) -> float:
        return 2 * self.commission_rate + 2 * self.slippage_rate + self.spread_estimate


@dataclass(frozen=True)
class RiskConfig:
    daily_loss_limit: float = 0.01
    consecutive_loss_limit: int = 3
    high_vol_quantile: float = 0.90


@dataclass(frozen=True)
class QuantConfig:
    watchlist: tuple[str, ...]
    data: DataConfig = DataConfig()
    label: LabelConfig = LabelConfig()
    model: ModelConfig = ModelConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    costs: CostConfig = CostConfig()
    risk: RiskConfig = RiskConfig()
    validation: ValidationConfig = ValidationConfig()
    data_dir: Path = BASE_DIR / "data" / "quant"
    reports_dir: Path = BASE_DIR / "reports" / "quant"
    feature_columns: tuple[str, ...] = DEFAULT_FEATURE_COLUMNS

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def predictions_dir(self) -> Path:
        return self.data_dir / "predictions"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    return value if isinstance(value, dict) else {}


def load_config(path: Path | str = CONFIG_PATH) -> QuantConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    raw = raw or {}
    watchlist = tuple(str(ticker).upper().strip() for ticker in raw.get("watchlist", []) if str(ticker).strip())
    if not watchlist:
        raise ValueError(f"No watchlist configured in {config_path}")
    return QuantConfig(
        watchlist=watchlist,
        data=DataConfig(**_section(raw, "data")),
        label=LabelConfig(**_section(raw, "label")),
        model=ModelConfig(**_section(raw, "model")),
        portfolio=PortfolioConfig(**_section(raw, "portfolio")),
        costs=CostConfig(**_section(raw, "costs")),
        risk=RiskConfig(**_section(raw, "risk")),
        validation=ValidationConfig(**_section(raw, "validation")),
    )


def ensure_directories(config: QuantConfig) -> None:
    for path in (config.raw_dir, config.processed_dir, config.predictions_dir, config.models_dir, config.reports_dir):
        path.mkdir(parents=True, exist_ok=True)
