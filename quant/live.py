from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yfinance as yf

from quant.config import QuantConfig, ensure_directories
from quant.data.loader import clean_intraday_bars, normalize_intraday
from quant.factors.calculator import add_minute_features

MARKET_TZ = "America/New_York"


def latest_completed_bar_time(now: pd.Timestamp | None = None) -> pd.Timestamp:
    current = pd.Timestamp.now(tz=MARKET_TZ) if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize(MARKET_TZ)
    else:
        current = current.tz_convert(MARKET_TZ)
    floored = current.floor("min")
    if current.second > 0 or current.microsecond > 0 or current.nanosecond > 0:
        return floored - pd.Timedelta(minutes=1)
    return floored - pd.Timedelta(minutes=1)


def parse_prediction_time(value: str | None, now: pd.Timestamp | None = None) -> pd.Timestamp:
    today = (pd.Timestamp.now(tz=MARKET_TZ) if now is None else pd.Timestamp(now).tz_convert(MARKET_TZ)).date()
    if not value:
        return latest_completed_bar_time(now).tz_localize(None)
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("--time must use HH:MM")
    requested = pd.Timestamp(year=today.year, month=today.month, day=today.day, hour=int(parts[0]), minute=int(parts[1]), tz=MARKET_TZ)
    return requested.tz_localize(None)


def _download_today(ticker: str) -> pd.DataFrame:
    raw = yf.download(ticker, period="5d", interval="1m", auto_adjust=False, progress=False)
    if raw.empty:
        raise ValueError(f"No Yahoo 1m data returned for {ticker}")
    return clean_intraday_bars(normalize_intraday(raw, ticker))


def load_live_bars(tickers: list[str], prediction_time: pd.Timestamp, config: QuantConfig) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for ticker in tickers:
        cache = config.processed_dir / f"{ticker.upper()}_1m.parquet"
        cached = pd.read_parquet(cache) if cache.exists() else pd.DataFrame()
        live = _download_today(ticker)
        combined = pd.concat([cached, live], ignore_index=True) if not cached.empty else live
        combined["timestamp"] = pd.to_datetime(combined["timestamp"])
        frames.append(combined.loc[combined["timestamp"] <= prediction_time])
    bars = clean_intraday_bars(pd.concat(frames, ignore_index=True))
    return bars.loc[bars["timestamp"] <= prediction_time].sort_values(["ticker", "timestamp"]).reset_index(drop=True)


def cost_adjustment(predicted_return: float, config: QuantConfig) -> dict[str, float]:
    round_trip = config.costs.round_trip_cost
    return {
        "commission_estimate": config.costs.commission_rate,
        "slippage_estimate": config.costs.slippage_rate,
        "spread_estimate": config.costs.spread_estimate,
        "safety_buffer": config.costs.safety_buffer,
        "estimated_round_trip_cost": round_trip,
        "net_expected_return": float(predicted_return - round_trip - config.costs.safety_buffer),
    }


def risk_filter(feature_row: pd.Series, history: pd.DataFrame, prediction_time: pd.Timestamp, warnings: list[str], config: QuantConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    minutes_to_close = float(feature_row.get("minutes_to_close", np.nan))
    if pd.isna(minutes_to_close) or minutes_to_close < config.label.horizon_minutes:
        reasons.append("distance_to_close_less_than_60_minutes")
        warnings.append("距离收盘不足60分钟，不能生成完整60分钟交易信号。")
    if "rolling_vol_60m" in feature_row and "rolling_vol_60m" in history:
        threshold = history["rolling_vol_60m"].dropna().quantile(config.risk.high_vol_quantile)
        if pd.notna(threshold) and pd.notna(feature_row["rolling_vol_60m"]) and feature_row["rolling_vol_60m"] > threshold:
            reasons.append("rolling_vol_60m_extreme")
            warnings.append("当前rolling_vol_60m超过历史风险阈值。")
    return not reasons, reasons


def _classes_from_model(model) -> np.ndarray:
    inner = model.named_steps["model"] if hasattr(model, "named_steps") else model
    classes = np.asarray(getattr(inner, "classes_", CLASS_ORDER))
    if set(classes.tolist()) == {0, 1, 2}:
        return pd.Series(classes).map({0: -1, 1: 0, 2: 1}).to_numpy()
    return classes


CLASS_ORDER = [-1, 0, 1]


def _probabilities(model, row: pd.DataFrame) -> dict[str, float]:
    probs = model.predict_proba(row)[0]
    classes = _classes_from_model(model)
    values = {}
    for klass, name in [(-1, "probability_down"), (0, "probability_flat"), (1, "probability_up")]:
        values[name] = float(probs[list(classes).index(klass)]) if klass in classes else 0.0
    values["predicted_class"] = int(max([(-1, values["probability_down"]), (0, values["probability_flat"]), (1, values["probability_up"])], key=lambda x: x[1])[0])
    return values


def _write_reports(report: dict[str, object], config: QuantConfig) -> tuple[Path, Path]:
    ensure_directories(config)
    timestamp = str(report["requested_prediction_time"]).replace(":", "").replace("-", "").replace(" ", "_")
    ticker = str(report["target_ticker"]).upper()
    json_path = config.reports_dir / f"live_prediction_{ticker}_{timestamp}.json"
    md_path = config.reports_dir / f"live_prediction_{ticker}_{timestamp}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    lines = [
        "# Intraday 60-Minute Prediction Report",
        "",
        "## Metadata",
        f"Prediction generated at: {report['prediction_generated_at']}",
        f"Market timezone: {MARKET_TZ}",
        f"Target ticker: {report['target_ticker']}",
        f"Requested prediction time: {report['requested_prediction_time']}",
        f"Latest completed bar: {report['latest_completed_bar']}",
        f"Model training end date: {report['model_training_end_date']}",
        f"Training tickers: {', '.join(report['training_tickers'])}",
        f"Training sample count: {report['training_sample_count']}",
        f"Model version: {report['model_version']}",
        "",
        "## Current Market Data",
        f"Current close: {report['current_close']}",
        f"QQQ current close: {report['qqq_current_close']}",
        f"SPY current close: {report['spy_current_close']}",
        f"Minutes from open: {report['minutes_from_open']}",
        f"Minutes to close: {report['minutes_to_close']}",
        "",
        "## Model Prediction",
        f"Predicted 60-minute return: {report['predicted_return_60m']}",
        f"Probability up: {report['probability_up']}",
        f"Probability flat: {report['probability_flat']}",
        f"Probability down: {report['probability_down']}",
        f"Predicted class: {report['predicted_class']}",
        "",
        "## Cost Adjustment",
        f"Commission estimate: {report['commission_estimate']}",
        f"Slippage estimate: {report['slippage_estimate']}",
        f"Spread estimate: {report['spread_estimate']}",
        f"Safety buffer: {report['safety_buffer']}",
        f"Estimated round-trip cost: {report['estimated_round_trip_cost']}",
        f"Net expected return: {report['net_expected_return']}",
        "",
        "## Signal",
        f"Raw model signal: {report['raw_model_signal']}",
        f"Research go/no-go: {report['research_go_no_go']}",
        f"Risk filter result: {report['risk_filter_result']}",
        f"Final auxiliary signal: {report['final_auxiliary_signal']}",
        f"Maximum forecast horizon: {report['maximum_forecast_horizon']}",
        f"Forecast target time: {report['forecast_target_time']}",
        "",
        "## Main Feature Values",
        *[f"- {k}: {v}" for k, v in report["main_feature_values"].items()],
        "",
        "## Model Explanation",
        *[f"- {k}: {v}" for k, v in report["model_explanation"].items()],
        "",
        "## Warnings",
        *[f"- {warning}" for warning in report["warnings"]],
    ]
    md_path.write_text("\n".join(lines))
    return md_path, json_path


def predict_live(config: QuantConfig, ticker: str = "SPCX", time_value: str | None = None) -> dict[str, object]:
    ensure_directories(config)
    ticker = ticker.upper().strip()
    prediction_time = parse_prediction_time(time_value)
    warnings = ["该系统仅用于研究和辅助判断，不构成投资建议。", "Yahoo Finance分钟数据可能延迟、缺失或被修订，不适合严肃实盘执行。"]
    features_path = config.models_dir / "feature_columns.json"
    metadata_path = config.models_dir / "model_metadata.json"
    if not features_path.exists() or not metadata_path.exists():
        raise FileNotFoundError("Models are not trained. Run `python -m quant.cli train-model` first.")
    feature_columns = tuple(json.loads(features_path.read_text()))
    metadata = json.loads(metadata_path.read_text())
    training_end = pd.to_datetime(metadata.get("training_end"))
    if pd.notna(training_end) and training_end > prediction_time:
        warnings.append("模型训练截止时间晚于请求预测时刻，请检查数据切分。")
    encoder = joblib.load(config.models_dir / "ticker_encoder.pkl")
    reg = joblib.load(config.models_dir / "regression_model.pkl")
    cls = joblib.load(config.models_dir / "classification_model.pkl")
    tickers = list(dict.fromkeys([ticker, "QQQ", "SPY"]))
    bars = load_live_bars(tickers, prediction_time, config)
    if bars.empty or ticker not in set(bars["ticker"]):
        raise ValueError(f"No completed bars available for {ticker} at or before {prediction_time}")
    features = add_minute_features(bars, encoder=encoder)
    target_rows = features.loc[(features["ticker"] == ticker) & (features["timestamp"] <= prediction_time)].sort_values("timestamp")
    if target_rows.empty:
        raise ValueError(f"No feature row available for {ticker} at or before {prediction_time}")
    row = target_rows.iloc[-1]
    latest_bar = pd.Timestamp(row["timestamp"])
    if latest_bar != prediction_time:
        warnings.append(f"请求时刻没有完整bar，使用最新已完成bar {latest_bar}。")
    x = pd.DataFrame([row.reindex(feature_columns)])
    predicted_return = float(reg.predict(x)[0])
    probs = _probabilities(cls, x)
    costs = cost_adjustment(predicted_return, config)
    passed_risk, risk_reasons = risk_filter(row, features.loc[features["timestamp"] < latest_bar], latest_bar, warnings, config)
    go_no_go = metadata.get("go_no_go", {})
    research_decision = go_no_go.get("decision", "NO_GO")
    if research_decision != "GO":
        warnings.append(f"研究框架当前结论为 {research_decision}，最终辅助信号强制为 NO_TRADE。")
    raw_model_signal = "LONG_CANDIDATE" if probs["probability_up"] >= 0.60 and predicted_return > 0 and costs["net_expected_return"] > 0 else "NO_TRADE"
    final_signal = "LONG_CANDIDATE" if raw_model_signal == "LONG_CANDIDATE" and passed_risk and research_decision == "GO" else "NO_TRADE"
    close_lookup = bars.sort_values("timestamp").groupby("ticker").tail(1).set_index("ticker")["close"]
    importance_path = config.reports_dir / "feature_importance.csv"
    explanation: dict[str, object] = {}
    if importance_path.exists():
        imp = pd.read_csv(importance_path).head(10)
        explanation["top_feature_importance"] = imp[["feature", "importance"]].to_dict(orient="records") if not imp.empty else []
    else:
        explanation["top_feature_importance"] = []
    main_features = {column: (None if pd.isna(row.get(column)) else float(row.get(column))) for column in feature_columns[:20] if column in row}
    report = {
        "prediction_generated_at": pd.Timestamp.utcnow().isoformat(),
        "market_timezone": MARKET_TZ,
        "target_ticker": ticker,
        "requested_prediction_time": prediction_time,
        "latest_completed_bar": latest_bar,
        "model_training_end_date": metadata.get("training_end"),
        "training_tickers": metadata.get("training_tickers", []),
        "training_sample_count": metadata.get("number_of_rows", 0),
        "model_version": metadata.get("generated_at"),
        "current_close": float(row["close"]),
        "qqq_current_close": float(close_lookup.get("QQQ", np.nan)),
        "spy_current_close": float(close_lookup.get("SPY", np.nan)),
        "minutes_from_open": float(row.get("minutes_from_open", np.nan)),
        "minutes_to_close": float(row.get("minutes_to_close", np.nan)),
        "predicted_return_60m": predicted_return,
        **probs,
        **costs,
        "raw_model_signal": raw_model_signal,
        "research_go_no_go": research_decision,
        "baseline_comparison": go_no_go.get("baseline_comparison", {}),
        "historical_bucket_context": {
            "top_decile_net_mean_return": go_no_go.get("top_decile_net_mean_return"),
            "bottom_decile_mean_return": go_no_go.get("bottom_decile_mean_return"),
        },
        "risk_filter_result": "PASS" if passed_risk else f"BLOCK: {', '.join(risk_reasons)}",
        "final_auxiliary_signal": final_signal,
        "maximum_forecast_horizon": f"{config.label.horizon_minutes} minutes",
        "forecast_target_time": latest_bar + pd.Timedelta(minutes=config.label.horizon_minutes),
        "main_feature_values": main_features,
        "model_explanation": explanation,
        "warnings": warnings,
    }
    md_path, json_path = _write_reports(report, config)
    report["markdown_report_path"] = str(md_path)
    report["json_report_path"] = str(json_path)
    return report
