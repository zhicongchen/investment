from __future__ import annotations

from dataclasses import replace

import pandas as pd

from quant.config import QuantConfig
from quant.dataset import build_dataset


def test_dataset_preserves_ticker_and_timestamp(tmp_path):
    config = replace(QuantConfig(watchlist=("AAA", "BBB"), data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"), feature_columns=("feature",))
    features = pd.DataFrame(
        {
            "ticker": ["BBB", "AAA"],
            "timestamp": [pd.Timestamp("2024-01-02 09:31"), pd.Timestamp("2024-01-02 09:30")],
            "feature": [2.0, 1.0],
        }
    )
    labels = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "timestamp": [pd.Timestamp("2024-01-02 09:30"), pd.Timestamp("2024-01-02 09:31")],
            "future_return_60m": [0.01, -0.01],
            "label": [1, -1],
        }
    )

    dataset = build_dataset(features, labels, config)

    assert dataset["ticker"].tolist() == ["AAA", "BBB"]
    assert dataset["timestamp"].is_monotonic_increasing

