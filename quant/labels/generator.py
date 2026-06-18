from __future__ import annotations

import numpy as np
import pandas as pd

from quant.config import QuantConfig, ensure_directories
from quant.factors.calculator import trading_date
from quant.io import save_frame


def _labels_for_day(group: pd.DataFrame, horizon_minutes: int, cost_threshold: float) -> pd.DataFrame:
    g = group.sort_values("timestamp")[["ticker", "timestamp", "close"]].copy()
    lookup = g.set_index("timestamp")["close"]
    target_timestamp = g["timestamp"] + pd.Timedelta(minutes=horizon_minutes)
    future_close = target_timestamp.map(lookup)
    g["forecast_target_timestamp"] = target_timestamp
    g["future_return_60m"] = future_close.to_numpy() / g["close"] - 1
    g["label_60m"] = np.select(
        [g["future_return_60m"] > cost_threshold, g["future_return_60m"] < -cost_threshold],
        [1, -1],
        default=0,
    )
    g = g.dropna(subset=["future_return_60m"])
    g["label"] = g["label_60m"]
    return g[["ticker", "timestamp", "forecast_target_timestamp", "future_return_60m", "label_60m", "label"]]


def build_minute_labels(bars: pd.DataFrame, horizon_minutes: int = 60, cost_threshold: float = 0.0015) -> pd.DataFrame:
    frame = bars.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["trading_date"] = trading_date(frame["timestamp"])
    pieces = []
    for _, group in frame.sort_values(["ticker", "timestamp"]).groupby(["ticker", "trading_date"], sort=False):
        pieces.append(_labels_for_day(group, horizon_minutes, cost_threshold))
    if not pieces:
        return pd.DataFrame(columns=["ticker", "timestamp", "forecast_target_timestamp", "future_return_60m", "label_60m", "label"])
    return pd.concat(pieces, ignore_index=True).sort_values(["timestamp", "ticker"]).reset_index(drop=True)


def save_labels(labels: pd.DataFrame, config: QuantConfig) -> None:
    ensure_directories(config)
    save_frame(labels, config.processed_dir / "labels.parquet")
