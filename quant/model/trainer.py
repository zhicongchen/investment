from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import platform

import joblib
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.preprocessing import StandardScaler

from quant.config import QuantConfig, ensure_directories
from quant.data.loader import ticker_coverage
from quant.factors.calculator import ticker_encoder
from quant.io import save_frame

CLASS_ORDER = [-1, 0, 1]


@dataclass(frozen=True)
class WalkForwardSplit:
    fold_id: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    purge_minutes: int = 60
    embargo_minutes: int = 60


def trading_days(timestamps: pd.Series) -> pd.Series:
    return pd.Series(pd.to_datetime(timestamps).dt.normalize().drop_duplicates().sort_values().to_list())


def walk_forward_splits(
    timestamps: pd.Series,
    train_days: int = 10,
    validation_days: int = 3,
    test_days: int = 2,
    purge_minutes: int = 60,
    embargo_minutes: int = 60,
) -> list[WalkForwardSplit]:
    days = trading_days(timestamps)
    if len(days) < 3:
        return []
    splits: list[WalkForwardSplit] = []
    if len(days) < train_days + validation_days + test_days:
        train_days = min(max(1, len(days) - validation_days - test_days), train_days)
    fold_id = 1
    test_end_idx = train_days + validation_days + test_days
    while test_end_idx <= len(days):
        start = 0 if True else test_end_idx - (train_days + validation_days + test_days)
        train = days.iloc[start : test_end_idx - validation_days - test_days]
        validation = days.iloc[test_end_idx - validation_days - test_days : test_end_idx - test_days]
        test = days.iloc[test_end_idx - test_days : test_end_idx]
        train_end = train.iloc[-1] + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        validation_start = validation.iloc[0] + pd.Timedelta(minutes=purge_minutes)
        validation_end = validation.iloc[-1] + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        test_start = test.iloc[0] + pd.Timedelta(minutes=purge_minutes + embargo_minutes)
        splits.append(
            WalkForwardSplit(
                fold_id=fold_id,
                train_start=train.iloc[0],
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
                test_start=test_start,
                test_end=test.iloc[-1] + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1),
                purge_minutes=purge_minutes,
                embargo_minutes=embargo_minutes,
            )
        )
        fold_id += 1
        test_end_idx += max(1, test_days)
    return splits


def make_regressor():
    try:
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            objective="huber",
            n_estimators=200,
            learning_rate=0.03,
            num_leaves=7,
            max_depth=3,
            min_data_in_leaf=100,
            feature_fraction=0.7,
            bagging_fraction=0.7,
            bagging_freq=1,
            lambda_l1=1.0,
            lambda_l2=5.0,
            random_state=42,
            deterministic=True,
            verbosity=-1,
        ), "lightgbm_conservative"
    except Exception:
        pass
    try:
        from xgboost import XGBRegressor

        return XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.04, subsample=0.8, colsample_bytree=0.8, objective="reg:squarederror", random_state=42), "xgboost"
    except Exception:
        return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, random_state=42), "sklearn_hist_gradient_boosting"


def make_classifier():
    try:
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=200,
            learning_rate=0.03,
            num_leaves=7,
            max_depth=3,
            min_data_in_leaf=100,
            feature_fraction=0.7,
            bagging_fraction=0.7,
            bagging_freq=1,
            lambda_l1=1.0,
            lambda_l2=5.0,
            class_weight="balanced",
            random_state=42,
            deterministic=True,
            verbosity=-1,
        ), "lightgbm_conservative"
    except Exception:
        pass
    try:
        from xgboost import XGBClassifier

        return XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.04, subsample=0.8, colsample_bytree=0.8, objective="multi:softprob", eval_metric="mlogloss", random_state=42), "xgboost"
    except Exception:
        return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, random_state=42), "sklearn_hist_gradient_boosting"


def _encode_labels(labels: pd.Series, model_name: str) -> pd.Series:
    return labels.map({-1: 0, 0: 1, 1: 2}) if model_name == "xgboost" else labels


def _decode_labels(labels: np.ndarray, model_name: str) -> np.ndarray:
    return pd.Series(labels).map({0: -1, 1: 0, 2: 1}).to_numpy() if model_name == "xgboost" else labels


def _pipeline(model) -> Pipeline:
    return Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler()), ("model", model)])


def _write_leakage_audit(config: QuantConfig) -> None:
    rows = [
        ("rolling/EMA/VWAP", "Grouped by ticker and trading date; uses rolling/ewm/cumsum ending at t", "low", "quant/factors/calculator.py"),
        ("future labels", "Labels use exact t+horizon timestamp within same ticker/day", "low", "quant/labels/generator.py"),
        ("market alignment", "QQQ/SPY features merged on same completed timestamp only", "low", "quant/factors/calculator.py"),
        ("preprocessing", "Imputer/scaler fit inside each train fold pipeline", "low", "quant/model/trainer.py"),
        ("winsorization", "Regression target clipping thresholds computed from train fold only", "low", "quant/model/trainer.py"),
        ("validation", "Complete-day purged walk-forward; no random shuffle", "low", "quant/model/trainer.py"),
        ("remaining limitation", "Only 7 trading days; adjacent labels overlap heavily", "unresolved_limit", "reports/quant/research_summary.md"),
    ]
    text = ["# Leakage Audit", "", "| Check | Current implementation | Risk | Code location |", "|---|---|---|---|"]
    text.extend(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows)
    text.append("")
    text.append("No known forward-looking feature construction was found after the current fixes. The main unresolved limitation is sample size, not leakage.")
    (config.reports_dir / "leakage_audit.md").write_text("\n".join(text))


def _fold_frame(data: pd.DataFrame, split: WalkForwardSplit, phase: str) -> pd.DataFrame:
    if phase == "train":
        return data[(data["timestamp"] >= split.train_start) & (data["timestamp"] <= split.train_end)]
    if phase == "validation":
        return data[(data["timestamp"] >= split.validation_start) & (data["timestamp"] <= split.validation_end)]
    return data[(data["timestamp"] >= split.test_start) & (data["timestamp"] <= split.test_end)]


def _fold_definition_rows(data: pd.DataFrame, splits: list[WalkForwardSplit]) -> list[dict[str, object]]:
    rows = []
    for split in splits:
        train = _fold_frame(data, split, "train")
        validation = _fold_frame(data, split, "validation")
        test = _fold_frame(data, split, "test")
        rows.append(
            {
                "fold_id": split.fold_id,
                "ticker_scope": "pooled",
                "train_start": split.train_start,
                "train_end": split.train_end,
                "validation_start": split.validation_start,
                "validation_end": split.validation_end,
                "test_start": split.test_start,
                "test_end": split.test_end,
                "train_rows": len(train),
                "validation_rows": len(validation),
                "test_rows": len(test),
                "purge_minutes": split.purge_minutes,
                "embargo_minutes": split.embargo_minutes,
            }
        )
    return rows


def _winsor_limits(values: pd.Series, lower: float = 0.01, upper: float = 0.99) -> tuple[float, float]:
    return float(values.quantile(lower)), float(values.quantile(upper))


def _clip(values: pd.Series, limits: tuple[float, float]) -> pd.Series:
    return values.clip(lower=limits[0], upper=limits[1])


def _regression_baselines(train: pd.DataFrame, frame: pd.DataFrame, target: str) -> dict[str, np.ndarray]:
    mean_value = float(train[target].mean())
    return {
        "zero": np.zeros(len(frame)),
        "historical_mean": np.full(len(frame), mean_value),
        "momentum": frame["ret_60m"].fillna(0).to_numpy(),
        "mean_reversion": (-frame["ret_60m"].fillna(0)).to_numpy(),
    }


def _classification_baselines(train: pd.DataFrame, frame: pd.DataFrame, label_col: str) -> dict[str, pd.DataFrame]:
    counts = train[label_col].value_counts(normalize=True)
    majority = int(counts.idxmax()) if not counts.empty else 0
    result = {}
    for name in ("majority", "uniform", "prior"):
        out = frame[["timestamp", "ticker", "future_return_60m", "label_60m"]].copy()
        if name == "majority":
            probs = {-1: 1.0 if majority == -1 else 0.0, 0: 1.0 if majority == 0 else 0.0, 1: 1.0 if majority == 1 else 0.0}
        elif name == "uniform":
            probs = {-1: 1 / 3, 0: 1 / 3, 1: 1 / 3}
        else:
            probs = {-1: float(counts.get(-1, 0.0)), 0: float(counts.get(0, 0.0)), 1: float(counts.get(1, 0.0))}
        out["probability_down"] = probs[-1]
        out["probability_flat"] = probs[0]
        out["probability_up"] = probs[1]
        out["predicted_class"] = max(probs, key=probs.get)
        result[name] = out
    return result


def _dynamic_label(frame: pd.DataFrame, k: float, cost_floor: float) -> pd.Series:
    threshold = np.maximum(cost_floor, k * frame["past_realized_vol_60m"].fillna(0))
    return pd.Series(np.select([frame["future_return_60m"] > threshold, frame["future_return_60m"] < -threshold], [1, -1], default=0), index=frame.index)


def _select_dynamic_k(train: pd.DataFrame, validation: pd.DataFrame, config: QuantConfig) -> float:
    best_k = float(config.validation.dynamic_k_values[0])
    best_score = -np.inf
    for k in config.validation.dynamic_k_values:
        train_labels = _dynamic_label(train, float(k), config.validation.dynamic_cost_floor)
        val_labels = _dynamic_label(validation, float(k), config.validation.dynamic_cost_floor)
        majority = train_labels.value_counts().idxmax()
        score = f1_score(val_labels, np.full(len(val_labels), majority), average="macro", zero_division=0)
        if score > best_score:
            best_score = score
            best_k = float(k)
    return best_k


def _pooled_scope_comparison(
    data: pd.DataFrame,
    splits: list[WalkForwardSplit],
    features: tuple[str, ...],
) -> pd.DataFrame:
    rows = []
    for split in splits:
        train = _fold_frame(data, split, "train")
        test = _fold_frame(data, split, "test")
        for scope, scoped_train in [("pooled", train), ("pooled_excluding_spcx", train[train["ticker"] != "SPCX"])]:
            if scoped_train.empty or test.empty:
                continue
            model = _pipeline(Ridge(alpha=10.0))
            model.fit(scoped_train[list(features)], scoped_train["future_return_60m"])
            pred = model.predict(test[list(features)])
            for ticker_scope, mask in [("all", pd.Series(True, index=test.index)), ("spcx_only", test["ticker"] == "SPCX"), ("non_spcx", test["ticker"] != "SPCX")]:
                if not mask.any():
                    continue
                rows.append({"fold_id": split.fold_id, "train_scope": scope, "test_scope": ticker_scope, **regression_metrics(test.loc[mask, "future_return_60m"], pred[mask.to_numpy()])})
    return pd.DataFrame(rows)


def _probability_frame(pipeline: Pipeline, model_name: str, test: pd.DataFrame, feature_columns: tuple[str, ...]) -> pd.DataFrame:
    probabilities = pipeline.predict_proba(test[list(feature_columns)])
    model = pipeline.named_steps["model"]
    classes = _decode_labels(np.asarray(model.classes_), model_name)
    result = test[["timestamp", "ticker", "future_return_60m", "label_60m"]].copy()
    for klass, name in [(-1, "probability_down"), (0, "probability_flat"), (1, "probability_up")]:
        result[name] = probabilities[:, list(classes).index(klass)] if klass in classes else 0.0
    result["predicted_class"] = _decode_labels(pipeline.predict(test[list(feature_columns)]), model_name)
    return result


def regression_metrics(actual: pd.Series, predicted: np.ndarray) -> dict[str, float]:
    metrics = {
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "r2": float(r2_score(actual, predicted)) if len(actual) > 1 else np.nan,
    }
    try:
        metrics["spearman"] = float(spearmanr(actual, predicted, nan_policy="omit").statistic)
    except Exception:
        metrics["spearman"] = np.nan
    try:
        metrics["pearson"] = float(pearsonr(actual, predicted)[0])
    except Exception:
        metrics["pearson"] = np.nan
    return metrics


def classification_metrics(actual: pd.Series, predicted: pd.Series, probabilities: pd.DataFrame) -> dict[str, object]:
    metrics: dict[str, object] = {
        "accuracy": float(accuracy_score(actual, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
        "macro_f1": float(f1_score(actual, predicted, average="macro", zero_division=0)),
        "precision_by_class": dict(zip([str(c) for c in CLASS_ORDER], precision_score(actual, predicted, labels=CLASS_ORDER, average=None, zero_division=0).tolist())),
        "recall_by_class": dict(zip([str(c) for c in CLASS_ORDER], recall_score(actual, predicted, labels=CLASS_ORDER, average=None, zero_division=0).tolist())),
        "confusion_matrix": confusion_matrix(actual, predicted, labels=CLASS_ORDER).tolist(),
    }
    p = probabilities[["probability_down", "probability_flat", "probability_up"]]
    try:
        metrics["log_loss"] = float(log_loss(actual, p, labels=CLASS_ORDER))
    except Exception:
        metrics["log_loss"] = np.nan
    try:
        y_bin = pd.get_dummies(actual).reindex(columns=CLASS_ORDER, fill_value=0)
        metrics["roc_auc"] = float(roc_auc_score(y_bin, p, multi_class="ovr"))
    except Exception:
        metrics["roc_auc"] = np.nan
    return metrics


def _bucket_returns(predictions: pd.DataFrame, score_col: str) -> pd.DataFrame:
    if predictions.empty or predictions[score_col].nunique(dropna=True) < 2:
        return pd.DataFrame()
    frame = predictions.copy()
    frame["bucket"] = pd.qcut(frame[score_col], q=min(10, frame[score_col].nunique()), duplicates="drop")
    return frame.groupby("bucket", observed=False).agg(rows=("future_return_60m", "size"), actual_mean_return=("future_return_60m", "mean"), up_rate=("label_60m", lambda s: float((s == 1).mean()))).reset_index()


def _feature_importance(pipeline: Pipeline, features: tuple[str, ...]) -> pd.DataFrame:
    model = pipeline.named_steps["model"]
    if hasattr(model, "feature_importances_"):
        return pd.DataFrame({"feature": features, "importance": model.feature_importances_}).sort_values("importance", ascending=False)
    return pd.DataFrame(columns=["feature", "importance"])


def _metadata(config: QuantConfig, data: pd.DataFrame, successful_tickers: list[str], failed_tickers: list[str], validation_metrics: list[dict[str, object]], test_metrics: list[dict[str, object]], model_params: dict[str, object]) -> dict[str, object]:
    coverage = ticker_coverage(data)
    return {
        "training_start": str(pd.to_datetime(data["timestamp"]).min()) if not data.empty else None,
        "training_end": str(pd.to_datetime(data["timestamp"]).max()) if not data.empty else None,
        "training_tickers": sorted(data["ticker"].unique().tolist()) if not data.empty else [],
        "successful_tickers": successful_tickers,
        "failed_tickers": failed_tickers,
        "ticker_coverage": coverage.to_dict(orient="records"),
        "number_of_rows": int(len(data)),
        "number_of_days": int(pd.to_datetime(data["timestamp"]).dt.normalize().nunique()) if not data.empty else 0,
        "feature_columns": list(config.feature_columns),
        "label_horizon_minutes": config.label.horizon_minutes,
        "cost_threshold": config.label.cost_threshold,
        "model_parameters": model_params,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "package_versions": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__},
        "research_disclaimer": "Research only. Not investment advice and not an executable trading system.",
    }


def _go_no_go_inputs(predictions: pd.DataFrame, baselines: pd.DataFrame, config: QuantConfig) -> dict[str, object]:
    if predictions.empty:
        return {"decision": "NO_GO", "reason": "no_out_of_sample_predictions"}
    top_n = max(1, int(len(predictions) * 0.10))
    top = predictions.nlargest(top_n, "predicted_return_60m")
    bottom = predictions.nsmallest(top_n, "predicted_return_60m")
    net_cost = config.costs.round_trip_cost + config.costs.safety_buffer
    top_net = float(top["future_return_60m"].mean() - net_cost)
    bottom_mean = float(bottom["future_return_60m"].mean())
    model_test = regression_metrics(predictions["future_return_60m"], predictions["predicted_return_60m"])
    baseline_best = {}
    if not baselines.empty:
        test_reg = baselines[(baselines["phase"] == "test") & (baselines["task"] == "regression") & (baselines["target"] == "raw_return")]
        if not test_reg.empty:
            best = test_reg.sort_values(["spearman", "rmse"], ascending=[False, True]).iloc[0]
            baseline_best = {"model": best["model"], "spearman": float(best["spearman"]), "rmse": float(best["rmse"])}
    model_beats_baseline = bool(baseline_best and model_test["spearman"] > baseline_best["spearman"] and model_test["rmse"] <= baseline_best["rmse"] * 1.05)
    day_means = predictions.assign(day=predictions["timestamp"].dt.normalize()).groupby("day")["future_return_60m"].mean()
    stable_days = bool(len(day_means) >= 2 and (day_means > 0).mean() >= 0.5)
    decision = "GO" if model_beats_baseline and top_net > 0 and stable_days else "NO_GO"
    return {
        "decision": decision,
        "baseline_comparison": {"model_test": model_test, "best_baseline": baseline_best, "model_beats_baseline": model_beats_baseline},
        "fold_stability": {"test_days": int(len(day_means)), "positive_day_fraction": float((day_means > 0).mean()) if len(day_means) else 0.0},
        "top_decile_net_mean_return": top_net,
        "bottom_decile_mean_return": bottom_mean,
        "net_cost_assumption": net_cost,
    }


def train_walk_forward(dataset: pd.DataFrame, config: QuantConfig) -> dict[str, pd.DataFrame]:
    ensure_directories(config)
    _write_leakage_audit(config)
    feature_columns = tuple(config.feature_columns)
    data = dataset.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    if "label_60m" not in data:
        data["label_60m"] = data["label"]
    data = data.replace([np.inf, -np.inf], np.nan)
    available_features = tuple(column for column in feature_columns if column in data.columns)
    data = data.dropna(subset=[*available_features, "future_return_60m", "label_60m"]).sort_values(["timestamp", "ticker"]).reset_index(drop=True)
    splits = walk_forward_splits(
        data["timestamp"],
        config.validation.train_min_days,
        config.validation.validation_days,
        config.validation.test_days,
        config.validation.purge_minutes,
        config.validation.embargo_minutes,
    )
    save_frame(pd.DataFrame(_fold_definition_rows(data, splits)), config.reports_dir / "fold_definition.csv")
    prediction_rows: list[pd.DataFrame] = []
    metric_rows: list[dict[str, object]] = []
    baseline_rows: list[dict[str, object]] = []
    class_ratio_rows: list[dict[str, object]] = []
    validation_metrics: list[dict[str, object]] = []
    test_metrics: list[dict[str, object]] = []
    importance_rows: list[pd.DataFrame] = []
    label_candidates = {
        "raw_return": "future_return_60m",
        "excess_return": "excess_return_vs_qqq_60m",
        "vol_scaled_return": "vol_scaled_return_60m",
        "vol_scaled_excess_return": "vol_scaled_excess_return_60m",
    }
    for split_id, split in enumerate(splits, start=1):
        train = _fold_frame(data, split, "train")
        validation = _fold_frame(data, split, "validation")
        test = _fold_frame(data, split, "test")
        if train.empty or validation.empty or test.empty:
            continue
        selected_k = _select_dynamic_k(train, validation, config)
        for phase_name, frame in [("train", train), ("validation", validation), ("test", test)]:
            dyn = _dynamic_label(frame, selected_k, config.validation.dynamic_cost_floor)
            for ticker, labels in dyn.groupby(frame["ticker"]):
                counts = labels.value_counts(normalize=True)
                class_ratio_rows.append({"fold_id": split_id, "phase": phase_name, "ticker": ticker, "k": selected_k, "down": counts.get(-1, 0.0), "flat": counts.get(0, 0.0), "up": counts.get(1, 0.0)})

        for target_name, target_col in label_candidates.items():
            if target_col not in train or train[target_col].isna().all():
                continue
            train_target = train[target_col].dropna()
            limits = _winsor_limits(train_target)
            for phase_name, frame in [("validation", validation), ("test", test)]:
                frame_target = frame[target_col]
                valid = frame_target.notna()
                if not valid.any():
                    continue
                for model_name, pred in _regression_baselines(train, frame, target_col).items():
                    metrics = regression_metrics(_clip(frame_target[valid], limits), pred[valid])
                    baseline_rows.append({"fold_id": split_id, "phase": phase_name, "task": "regression", "target": target_name, "model": model_name, **metrics})
            ridge = _pipeline(Ridge(alpha=10.0))
            ridge.fit(train[list(available_features)], _clip(train[target_col], limits))
            for phase_name, frame in [("validation", validation), ("test", test)]:
                valid = frame[target_col].notna()
                if valid.any():
                    pred = ridge.predict(frame[list(available_features)])
                    baseline_rows.append({"fold_id": split_id, "phase": phase_name, "task": "regression", "target": target_name, "model": "ridge", **regression_metrics(_clip(frame.loc[valid, target_col], limits), pred[valid])})

        reg_model, reg_name = make_regressor()
        cls_model, cls_name = make_classifier()
        reg = _pipeline(reg_model)
        cls = _pipeline(cls_model)
        raw_limits = _winsor_limits(train["future_return_60m"])
        reg.fit(train[list(available_features)], _clip(train["future_return_60m"], raw_limits))
        cls_train_labels = _dynamic_label(train, selected_k, config.validation.dynamic_cost_floor)
        cls.fit(train[list(available_features)], _encode_labels(cls_train_labels, cls_name))
        logistic = _pipeline(LogisticRegression(max_iter=1000, class_weight="balanced"))
        logistic.fit(train[list(available_features)], cls_train_labels)
        for phase, frame in [("validation", validation), ("test", test)]:
            reg_pred = reg.predict(frame[list(available_features)])
            cls_frame = frame.copy()
            cls_frame["label_60m"] = _dynamic_label(frame, selected_k, config.validation.dynamic_cost_floor)
            cls_pred = _probability_frame(cls, cls_name, cls_frame, available_features)
            cls_pred["predicted_return_60m"] = reg_pred
            cls_pred["split_id"] = split_id
            cls_pred["phase"] = phase
            cls_pred["selected_k"] = selected_k
            cls_pred["dynamic_label_60m"] = cls_frame["label_60m"].to_numpy()
            for model_name, base_pred in _regression_baselines(train, frame, "future_return_60m").items():
                cls_pred[f"baseline_{model_name}_return"] = base_pred
            if phase == "test":
                prediction_rows.append(cls_pred)
            for model_name, base_cls in _classification_baselines(train.assign(label_60m=cls_train_labels), cls_frame, "label_60m").items():
                baseline_rows.append({"fold_id": split_id, "phase": phase, "task": "classification", "target": "dynamic_label", "model": model_name, **classification_metrics(cls_frame["label_60m"], base_cls["predicted_class"], base_cls)})
            log_pred = _probability_frame(logistic, "", cls_frame, available_features)
            baseline_rows.append({"fold_id": split_id, "phase": phase, "task": "classification", "target": "dynamic_label", "model": "logistic_balanced", **classification_metrics(cls_frame["label_60m"], log_pred["predicted_class"], log_pred)})
            metrics = {
                "split_id": split_id,
                "phase": phase,
                "selected_k": selected_k,
                "regression_model": reg_name,
                "classification_model": cls_name,
                "train_start": split.train_start,
                "train_end": split.train_end,
                "validation_start": split.validation_start,
                "validation_end": split.validation_end,
                "test_start": split.test_start,
                "test_end": split.test_end,
                **{f"reg_{k}": v for k, v in regression_metrics(frame["future_return_60m"], reg_pred).items()},
                **{f"cls_{k}": v for k, v in classification_metrics(cls_frame["label_60m"], cls_pred["predicted_class"], cls_pred).items()},
            }
            metric_rows.append(metrics)
            (validation_metrics if phase == "validation" else test_metrics).append(metrics)
        imp = _feature_importance(reg, available_features)
        if not imp.empty:
            imp["split_id"] = split_id
            imp["model"] = "regression"
            importance_rows.append(imp)
    predictions = pd.concat(prediction_rows, ignore_index=True) if prediction_rows else pd.DataFrame()
    metrics = pd.DataFrame(metric_rows)
    baselines = pd.DataFrame(baseline_rows)
    class_ratios = pd.DataFrame(class_ratio_rows)
    importance = pd.concat(importance_rows, ignore_index=True) if importance_rows else pd.DataFrame(columns=["feature", "importance", "split_id", "model"])
    save_frame(predictions, config.predictions_dir / "predictions.csv")
    save_frame(metrics, config.reports_dir / "training_metrics.csv")
    save_frame(metrics, config.reports_dir / "fold_metrics.csv")
    save_frame(baselines, config.reports_dir / "baseline_metrics.csv")
    save_frame(class_ratios, config.reports_dir / "dynamic_label_class_ratios.csv")
    scope_comparison = _pooled_scope_comparison(data, splits, available_features)
    save_frame(scope_comparison, config.reports_dir / "pooled_scope_comparison.csv")
    save_frame(importance, config.reports_dir / "feature_importance.csv")
    bucket = _bucket_returns(predictions, "predicted_return_60m") if not predictions.empty else pd.DataFrame()
    if not bucket.empty:
        save_frame(bucket, config.reports_dir / "predicted_return_buckets.csv")

    if data.empty:
        raise ValueError("Dataset has no complete feature/label rows")
    encoder = ticker_encoder(data["ticker"])
    reg_model, reg_name = make_regressor()
    cls_model, cls_name = make_classifier()
    final_reg = _pipeline(reg_model)
    final_cls = _pipeline(cls_model)
    final_reg.fit(data[list(available_features)], data["future_return_60m"])
    selected_k = float(metrics["selected_k"].dropna().mode().iloc[0]) if "selected_k" in metrics and not metrics.empty else 0.5
    final_labels = _dynamic_label(data, selected_k, config.validation.dynamic_cost_floor)
    final_cls.fit(data[list(available_features)], _encode_labels(final_labels, cls_name))
    joblib.dump(final_reg, config.models_dir / "regression_model.pkl")
    joblib.dump(final_cls, config.models_dir / "classification_model.pkl")
    joblib.dump(final_reg[:-1], config.models_dir / "preprocessor.pkl")
    joblib.dump(encoder, config.models_dir / "ticker_encoder.pkl")
    (config.models_dir / "feature_columns.json").write_text(json.dumps(list(available_features), indent=2))
    go_inputs = _go_no_go_inputs(predictions, baselines, config)
    metadata = _metadata(
        config,
        data,
        successful_tickers=sorted(data["ticker"].unique().tolist()),
        failed_tickers=[],
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        model_params={"regression_model": reg_name, "classification_model": cls_name, "selected_dynamic_k": selected_k},
    )
    metadata["selected_regression_label"] = "raw_return"
    metadata["selected_classification_label"] = "dynamic_label_60m"
    metadata["baseline_comparison"] = go_inputs.get("baseline_comparison", {})
    metadata["fold_stability"] = go_inputs.get("fold_stability", {})
    metadata["go_no_go"] = go_inputs
    (config.models_dir / "model_metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    return {"predictions": predictions, "metrics": metrics, "feature_importance": importance, "baseline_metrics": baselines}


def evaluate_saved_predictions(config: QuantConfig) -> dict[str, pd.DataFrame]:
    predictions = pd.read_csv(config.predictions_dir / "predictions.csv")
    predictions["timestamp"] = pd.to_datetime(predictions["timestamp"])
    by_ticker = predictions.groupby("ticker").apply(lambda g: pd.Series(regression_metrics(g["future_return_60m"], g["predicted_return_60m"])), include_groups=False).reset_index()
    predictions["trading_date"] = predictions["timestamp"].dt.normalize()
    by_day = predictions.groupby("trading_date").apply(lambda g: pd.Series(regression_metrics(g["future_return_60m"], g["predicted_return_60m"])), include_groups=False).reset_index()
    predictions["hour"] = predictions["timestamp"].dt.hour
    by_hour = predictions.groupby("hour").apply(lambda g: pd.Series(regression_metrics(g["future_return_60m"], g["predicted_return_60m"])), include_groups=False).reset_index()
    prob_bucket = _bucket_returns(predictions, "probability_up")
    ret_bucket = _bucket_returns(predictions, "predicted_return_60m")
    predictions["net_realized_return"] = predictions["future_return_60m"] - config.costs.round_trip_cost - config.costs.safety_buffer
    predictions["model_signal"] = np.where((predictions["probability_up"] >= 0.60) & (predictions["predicted_return_60m"] > 0), "LONG_CANDIDATE", "NO_TRADE")
    signal_backtest = (
        predictions.groupby("model_signal")
        .agg(rows=("future_return_60m", "size"), avg_future_return=("future_return_60m", "mean"), avg_net_return=("net_realized_return", "mean"), hit_rate=("future_return_60m", lambda s: float((s > 0).mean())))
        .reset_index()
    )
    baselines = pd.read_csv(config.reports_dir / "baseline_metrics.csv") if (config.reports_dir / "baseline_metrics.csv").exists() else pd.DataFrame()
    go_inputs = _go_no_go_inputs(predictions, baselines, config)
    metadata_path = config.models_dir / "model_metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        metadata["go_no_go"] = go_inputs
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str))
    save_frame(by_ticker, config.reports_dir / "oos_by_ticker.csv")
    save_frame(by_day, config.reports_dir / "oos_by_day.csv")
    save_frame(by_hour, config.reports_dir / "oos_by_hour.csv")
    save_frame(signal_backtest, config.reports_dir / "signal_backtest.csv")
    if not prob_bucket.empty:
        save_frame(prob_bucket, config.reports_dir / "probability_up_buckets.csv")
    if not ret_bucket.empty:
        save_frame(ret_bucket, config.reports_dir / "predicted_return_buckets.csv")
    _write_research_summary(config, predictions, by_ticker, by_day, by_hour, signal_backtest, baselines, go_inputs)
    return {"by_ticker": by_ticker, "by_day": by_day, "by_hour": by_hour, "probability_up_buckets": prob_bucket, "predicted_return_buckets": ret_bucket, "signal_backtest": signal_backtest}


def _write_research_summary(
    config: QuantConfig,
    predictions: pd.DataFrame,
    by_ticker: pd.DataFrame,
    by_day: pd.DataFrame,
    by_hour: pd.DataFrame,
    signal_backtest: pd.DataFrame,
    baselines: pd.DataFrame,
    go_inputs: dict[str, object],
) -> None:
    coverage_path = config.reports_dir / "ticker_coverage.csv"
    coverage = pd.read_csv(coverage_path) if coverage_path.exists() else pd.DataFrame()
    lines = [
        "# Small-Sample Minute Research Summary",
        "",
        f"Decision: **{go_inputs.get('decision', 'NO_GO')}**",
        "",
        "## Data Scope",
        "```",
        coverage.to_csv(index=False).strip() if not coverage.empty else "Coverage report unavailable.",
        "```",
        "",
        "## Leakage Audit",
        "See `reports/quant/leakage_audit.md`. Current implementation uses same-day causal rolling features, same-timestamp market joins, and train-fold-only preprocessing.",
        "",
        "## Baseline Comparison",
    ]
    if not baselines.empty:
        test_reg = baselines[(baselines["phase"] == "test") & (baselines["task"] == "regression") & (baselines["target"] == "raw_return")]
        lines.extend(["```", test_reg[["model", "mae", "rmse", "r2", "spearman", "pearson"]].sort_values("spearman", ascending=False).to_csv(index=False).strip() if not test_reg.empty else "No regression baseline rows.", "```"])
    else:
        lines.append("No baseline rows available.")
    lines.extend(
        [
            "",
            "## Out-of-Sample By Ticker",
            "```",
            by_ticker.to_csv(index=False).strip(),
            "```",
            "",
            "## Signal Backtest With Costs",
            "```",
            signal_backtest.to_csv(index=False).strip(),
            "```",
            "",
            "## Go / No-Go Inputs",
            f"- Top decile net mean return: {go_inputs.get('top_decile_net_mean_return')}",
            f"- Model beats baseline: {go_inputs.get('baseline_comparison', {}).get('model_beats_baseline')}",
            f"- Positive day fraction: {go_inputs.get('fold_stability', {}).get('positive_day_fraction')}",
            "",
            "## Limitations",
            "- Only the currently cached 7-day minute sample is used.",
            "- SPCX has fewer available trading days than the rest of the universe.",
            "- Adjacent 60-minute labels overlap heavily, so evidence is not independent.",
            "- This is research output only and not investment advice.",
        ]
    )
    (config.reports_dir / "research_summary.md").write_text("\n".join(lines))


def predict_with_latest_model(dataset: pd.DataFrame, config: QuantConfig) -> pd.DataFrame:
    reg = joblib.load(Path(config.models_dir) / "regression_model.pkl")
    cls = joblib.load(Path(config.models_dir) / "classification_model.pkl")
    features = tuple(json.loads((Path(config.models_dir) / "feature_columns.json").read_text()))
    data = dataset.dropna(subset=list(features)).sort_values(["timestamp", "ticker"])
    probabilities = _probability_frame(cls, "xgboost" if cls.named_steps["model"].__class__.__name__.startswith("XGB") else "", data, features)
    probabilities["predicted_return_60m"] = reg.predict(data[list(features)])
    save_frame(probabilities, config.predictions_dir / "predictions.csv")
    return probabilities
