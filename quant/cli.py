from __future__ import annotations

import argparse

import pandas as pd

from quant.backtest.engine import run_backtest
from quant.config import load_config
from quant.data.loader import download_intraday_data, load_processed_bars
from quant.dataset import build_dataset, save_dataset
from quant.factors.calculator import add_minute_features, save_features
from quant.io import load_frame
from quant.labels.generator import build_minute_labels, save_labels
from quant.live import predict_live
from quant.model.trainer import evaluate_saved_predictions, predict_with_latest_model, train_walk_forward
from quant.signals import generate_signals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research-only minute-level quant system")
    parser.add_argument("--config", default="quant/config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("download-data")
    subparsers.add_parser("build-dataset")
    subparsers.add_parser("train-model")
    subparsers.add_parser("evaluate")
    subparsers.add_parser("predict")
    live = subparsers.add_parser("predict-live")
    live.add_argument("--ticker", default="SPCX")
    live.add_argument("--time", default=None)
    subparsers.add_parser("generate-signals")
    subparsers.add_parser("backtest")
    subparsers.add_parser("full-train")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.command == "download-data":
        bars = download_intraday_data(config)
        print(f"Downloaded and cleaned {len(bars)} one-minute bars")
    elif args.command == "build-dataset":
        bars = load_processed_bars(config)
        features = add_minute_features(bars)
        labels = build_minute_labels(bars, config.label.horizon_minutes, config.label.cost_threshold)
        dataset = build_dataset(features, labels, config)
        save_features(features, config)
        save_labels(labels, config)
        save_dataset(dataset, config)
        print(f"Saved dataset with {len(dataset)} rows")
    elif args.command == "train-model":
        dataset = load_frame(config.processed_dir / "dataset.parquet")
        dataset["timestamp"] = pd.to_datetime(dataset["timestamp"])
        result = train_walk_forward(dataset, config)
        print(f"Saved {len(result['predictions'])} walk-forward predictions")
    elif args.command == "evaluate":
        result = evaluate_saved_predictions(config)
        print(f"Saved evaluation reports: {', '.join(result.keys())}")
    elif args.command == "predict":
        dataset = load_frame(config.processed_dir / "dataset.parquet")
        dataset["timestamp"] = pd.to_datetime(dataset["timestamp"])
        predictions = predict_with_latest_model(dataset, config)
        print(f"Saved {len(predictions)} predictions")
    elif args.command == "predict-live":
        report = predict_live(config, args.ticker, args.time)
        print(f"Final auxiliary signal: {report['final_auxiliary_signal']}")
        print(f"Markdown report: {report['markdown_report_path']}")
        print(f"JSON report: {report['json_report_path']}")
    elif args.command == "generate-signals":
        predictions = load_frame(config.predictions_dir / "predictions.csv")
        predictions["timestamp"] = pd.to_datetime(predictions["timestamp"])
        signals = generate_signals(predictions, config)
        print(f"Saved {len(signals)} signals")
    elif args.command == "backtest":
        bars = load_processed_bars(config)
        features = load_frame(config.processed_dir / "features.parquet")
        signals = load_frame(config.predictions_dir / "signals.csv")
        features["timestamp"] = pd.to_datetime(features["timestamp"])
        signals["timestamp"] = pd.to_datetime(signals["timestamp"])
        result = run_backtest(bars, features, signals, config)
        print(result["metrics"].to_string(index=False))
    elif args.command == "full-train":
        bars = download_intraday_data(config)
        features = add_minute_features(bars)
        labels = build_minute_labels(bars, config.label.horizon_minutes, config.label.cost_threshold)
        dataset = build_dataset(features, labels, config)
        save_features(features, config)
        save_labels(labels, config)
        save_dataset(dataset, config)
        result = train_walk_forward(dataset, config)
        evaluate_saved_predictions(config)
        print(f"Full training complete with {len(dataset)} rows and {len(result['predictions'])} out-of-sample predictions")


if __name__ == "__main__":
    main()
