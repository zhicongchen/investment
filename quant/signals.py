from __future__ import annotations

import pandas as pd

from quant.config import QuantConfig, ensure_directories
from quant.io import save_frame


def generate_signals(predictions: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    signals = predictions.copy()
    signals["signal"] = "NO_TRADE"
    signals.loc[signals["probability_up"] > config.model.probability_threshold, "signal"] = "LONG"
    signals = signals.sort_values(["timestamp", "probability_up"], ascending=[True, False]).reset_index(drop=True)
    ensure_directories(config)
    save_frame(signals, config.predictions_dir / "signals.csv")
    return signals


def select_portfolio(signals_at_time: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    longs = signals_at_time.loc[signals_at_time["signal"] == "LONG"].sort_values("probability_up", ascending=False)
    longs = longs.drop_duplicates("ticker").head(config.portfolio.max_positions).copy()
    longs["target_weight"] = config.portfolio.max_position_size
    return longs
