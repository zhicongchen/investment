from __future__ import annotations

import pandas as pd

from quant.model.trainer import walk_forward_splits


def test_walk_forward_splits_do_not_overlap():
    days = pd.bdate_range("2024-01-01", periods=25)
    timestamps = pd.Series(
        [
            timestamp
            for day in days
            for timestamp in pd.date_range(day + pd.Timedelta(hours=9, minutes=30), periods=390, freq="min")
        ]
    )

    splits = walk_forward_splits(timestamps, train_days=10, validation_days=5, test_days=5)

    assert splits
    first = splits[0]
    assert first.train_end < first.validation_start
    assert first.validation_end < first.test_start
