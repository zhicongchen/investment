from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from quant.config import QuantConfig, ensure_directories
from quant.io import save_frame


def merge_features_labels(factors: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    return factors.merge(labels, on=["ticker", "date"], how="inner")


def factor_ic(data: pd.DataFrame, factor_columns: list[str], label_column: str = "future_return_60m") -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date, group in data.groupby("date"):
        for factor in factor_columns:
            sample = group[[factor, label_column]].dropna()
            if len(sample) < 3 or sample[factor].nunique() < 2:
                continue
            ic = sample[factor].corr(sample[label_column])
            rank_ic = spearmanr(sample[factor], sample[label_column]).correlation
            rows.append({"date": date, "factor": factor, "ic": ic, "rank_ic": rank_ic})
    return pd.DataFrame(rows)


def quantile_returns(data: pd.DataFrame, factor: str, label_column: str = "future_return_60m") -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date, group in data.groupby("date"):
        sample = group[[factor, label_column]].dropna()
        if len(sample) < 5:
            continue
        ranked = sample.sort_values(factor)
        n = len(ranked)
        bottom = ranked.iloc[: max(1, int(n * 0.2))]
        top = ranked.iloc[-max(1, int(n * 0.2)) :]
        middle = ranked.iloc[max(1, int(n * 0.4)) : max(1, int(n * 0.6))]
        rows.extend(
            [
                {"date": date, "factor": factor, "bucket": "bottom_20", "return": bottom[label_column].mean()},
                {"date": date, "factor": factor, "bucket": "middle", "return": middle[label_column].mean()},
                {"date": date, "factor": factor, "bucket": "top_20", "return": top[label_column].mean()},
            ]
        )
    return pd.DataFrame(rows)


def summarize_ic(ic: pd.DataFrame) -> pd.DataFrame:
    if ic.empty:
        return pd.DataFrame(columns=["factor", "mean_ic", "mean_rank_ic", "icir", "observations"])
    grouped = ic.groupby("factor")
    return grouped.agg(mean_ic=("ic", "mean"), mean_rank_ic=("rank_ic", "mean"), observations=("ic", "count")).assign(
        icir=lambda frame: frame["mean_ic"] / grouped["ic"].std().replace(0, np.nan)
    ).reset_index()


def run_factor_research(factors: pd.DataFrame, labels: pd.DataFrame, config: QuantConfig) -> dict[str, pd.DataFrame]:
    ensure_directories(config)
    data = merge_features_labels(factors, labels)
    factor_columns = [column for column in config.feature_columns if column in data.columns]
    ic = factor_ic(data, factor_columns)
    summary = summarize_ic(ic)
    buckets = pd.concat([quantile_returns(data, factor) for factor in factor_columns], ignore_index=True)
    save_frame(ic, config.reports_dir / "factor_ic.csv")
    save_frame(summary, config.reports_dir / "factor_ic_summary.csv")
    save_frame(buckets, config.reports_dir / "factor_quantile_returns.csv")
    return {"ic": ic, "summary": summary, "buckets": buckets}
