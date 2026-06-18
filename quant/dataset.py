from __future__ import annotations

import pandas as pd
import numpy as np

from quant.config import QuantConfig, ensure_directories
from quant.io import save_frame


def _add_research_labels(dataset: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    result = dataset.copy()
    qqq = result.loc[result["ticker"] == "QQQ", ["timestamp", "future_return_60m"]].rename(columns={"future_return_60m": "qqq_future_return_60m"})
    spy = result.loc[result["ticker"] == "SPY", ["timestamp", "future_return_60m"]].rename(columns={"future_return_60m": "spy_future_return_60m"})
    result = result.merge(qqq, on="timestamp", how="left").merge(spy, on="timestamp", how="left")
    benchmark = result["qqq_future_return_60m"]
    benchmark = benchmark.where(result["ticker"] != "QQQ", result["spy_future_return_60m"])
    result["excess_return_vs_qqq_60m"] = result["future_return_60m"] - benchmark
    result["past_realized_vol_60m"] = result.get("realized_vol_60", result.get("rolling_vol_60m"))
    epsilon = 1e-6
    denom = result["past_realized_vol_60m"].clip(lower=epsilon)
    result["vol_scaled_return_60m"] = result["future_return_60m"] / denom
    result["vol_scaled_excess_return_60m"] = result["excess_return_vs_qqq_60m"] / denom
    threshold = np.maximum(config.validation.dynamic_cost_floor, 0.5 * result["past_realized_vol_60m"].fillna(0))
    result["dynamic_threshold_60m"] = threshold
    result["dynamic_label_60m"] = np.select([result["future_return_60m"] > threshold, result["future_return_60m"] < -threshold], [1, -1], default=0)
    result["tradable_up"] = (result["future_return_60m"] > threshold).astype(int)
    result["tradable_down"] = (result["future_return_60m"] < -threshold).astype(int)
    return result


def _write_feature_reports(features: pd.DataFrame, config: QuantConfig) -> None:
    numeric = features.select_dtypes(include="number")
    dictionary = []
    for column in features.columns:
        if column in {"ticker", "timestamp"}:
            continue
        dictionary.append(
            {
                "feature": column,
                "source": "current_or_past_ohlcv_market_cross_section",
                "causal": True,
                "formula": "See quant/factors/calculator.py",
            }
        )
    save_frame(pd.DataFrame(dictionary), config.reports_dir / "feature_dictionary.csv")
    missingness = features.isna().mean().reset_index()
    missingness.columns = ["feature", "missing_fraction"]
    save_frame(missingness.sort_values("missing_fraction", ascending=False), config.reports_dir / "feature_missingness.csv")
    corr = numeric.corr().abs()
    pairs = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool)).stack().reset_index()
    pairs.columns = ["feature_a", "feature_b", "abs_correlation"]
    save_frame(pairs.sort_values("abs_correlation", ascending=False).head(500), config.reports_dir / "feature_correlation.csv")


def build_dataset(features: pd.DataFrame, labels: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    ensure_directories(config)
    _write_feature_reports(features, config)
    dataset = features.merge(labels, on=["ticker", "timestamp"], how="inner")
    if "label_60m" not in dataset and "label" in dataset:
        dataset["label_60m"] = dataset["label"]
    if "label" not in dataset and "label_60m" in dataset:
        dataset["label"] = dataset["label_60m"]
    dataset = _add_research_labels(dataset, config)
    optional = [
        column
        for column in [
            "forecast_target_timestamp",
            "qqq_future_return_60m",
            "spy_future_return_60m",
            "excess_return_vs_qqq_60m",
            "past_realized_vol_60m",
            "vol_scaled_return_60m",
            "vol_scaled_excess_return_60m",
            "dynamic_threshold_60m",
            "dynamic_label_60m",
            "tradable_up",
            "tradable_down",
        ]
        if column in dataset.columns
    ]
    required = ["ticker", "timestamp", *optional, *config.feature_columns, "future_return_60m", "label_60m", "label"]
    required = list(dict.fromkeys(required))
    dataset = dataset[required].dropna(subset=[*config.feature_columns, "future_return_60m", "label_60m"])
    return dataset.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


def save_dataset(dataset: pd.DataFrame, config: QuantConfig) -> None:
    ensure_directories(config)
    save_frame(dataset, config.processed_dir / "dataset.parquet")
