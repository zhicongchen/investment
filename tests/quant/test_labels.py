from __future__ import annotations

import pandas as pd
import pytest

from quant.labels.generator import build_minute_labels


def test_label_shift_and_classification_are_correct():
    prices = [100] * 61 + [101]
    frame = pd.DataFrame(
        {
            "ticker": "AAA",
            "timestamp": pd.date_range("2024-01-02 09:30", periods=len(prices), freq="min"),
            "close": prices,
        }
    )

    labels = build_minute_labels(frame, horizon_minutes=60, cost_threshold=0.0015)

    assert labels.loc[0, "future_return_60m"] == pytest.approx(0.0)
    assert labels.loc[1, "future_return_60m"] == pytest.approx(0.01)
    assert labels.loc[1, "label"] == 1
    assert len(labels) == 2


def test_labels_do_not_cross_days_or_tickers():
    rows = []
    for ticker, start in [("AAA", 100), ("BBB", 200)]:
        for timestamp, price in [
            (pd.Timestamp("2024-01-02 15:30"), start),
            (pd.Timestamp("2024-01-03 09:30"), start + 10),
            (pd.Timestamp("2024-01-03 10:30"), start + 20),
        ]:
            rows.append({"ticker": ticker, "timestamp": timestamp, "close": price})
    labels = build_minute_labels(pd.DataFrame(rows), horizon_minutes=60, cost_threshold=0.0015)

    assert pd.Timestamp("2024-01-02 15:30") not in set(labels["timestamp"])
    aaa = labels.loc[labels["ticker"] == "AAA"].iloc[0]
    assert aaa["timestamp"] == pd.Timestamp("2024-01-03 09:30")
    assert aaa["future_return_60m"] == pytest.approx((120 / 110) - 1)
