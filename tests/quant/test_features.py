from __future__ import annotations

import pandas as pd

from quant.factors.calculator import add_minute_features


def bars(values: list[float], ticker: str = "AAA") -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-02 09:30", periods=len(values), freq="min")
    return pd.DataFrame(
        {
            "ticker": ticker,
            "timestamp": timestamps,
            "open": values,
            "high": [value + 0.5 for value in values],
            "low": [value - 0.5 for value in values],
            "close": values,
            "volume": [1000] * len(values),
        }
    )


def test_minute_features_have_expected_columns():
    features = add_minute_features(bars(list(range(100, 200))))

    for column in ["ret_1m", "ret_60m", "rolling_vol_60m", "volume_ratio_20m", "ma_60m_gap", "RSI_14", "ATR_14", "minute_of_day"]:
        assert column in features.columns


def test_features_do_not_use_future_data():
    base = list(range(100, 220))
    changed = base.copy()
    changed[-1] = 10_000

    first = add_minute_features(bars(base))
    second = add_minute_features(bars(changed))

    pd.testing.assert_series_equal(first.loc[: len(base) - 2, "ret_60m"], second.loc[: len(base) - 2, "ret_60m"])


def test_future_data_changes_do_not_change_past_features():
    base = bars(list(range(100, 240)))
    changed = base.copy()
    cutoff = pd.Timestamp("2024-01-02 10:30")
    changed.loc[changed["timestamp"] > cutoff, ["close", "volume"]] = [9999, 999999]

    first = add_minute_features(base)
    second = add_minute_features(changed)
    columns = ["ret_1m", "ret_60m", "rolling_vol_60m", "volume_ratio_20m", "RSI_14"]
    before = first["timestamp"] <= cutoff

    pd.testing.assert_frame_equal(first.loc[before, columns].reset_index(drop=True), second.loc[before, columns].reset_index(drop=True))


def test_market_features_align_by_timestamp():
    frame = pd.concat([bars(list(range(100, 220)), "AAA"), bars(list(range(200, 320)), "QQQ"), bars(list(range(300, 420)), "SPY")])
    features = add_minute_features(frame)
    row = features.loc[(features["ticker"] == "AAA") & (features["timestamp"] == pd.Timestamp("2024-01-02 10:30"))].iloc[0]
    qqq_row = features.loc[(features["ticker"] == "QQQ") & (features["timestamp"] == pd.Timestamp("2024-01-02 10:30"))].iloc[0]

    assert row["qqq_ret_5m"] == qqq_row["ret_5m"]
    assert row["ticker_ret_5m_minus_qqq"] == row["ret_5m"] - qqq_row["ret_5m"]
