from __future__ import annotations

from dataclasses import dataclass
import logging
import random
import time

import pandas as pd
import yfinance as yf

from quant.config import QuantConfig, ensure_directories
from quant.io import save_frame


LOGGER = logging.getLogger(__name__)
BAR_COLUMNS = ["ticker", "timestamp", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class ValidationReport:
    rows: int
    missing_values: int
    duplicate_rows: int
    non_positive_prices: int
    high_below_low: int
    missing_volume: int

    @property
    def ok(self) -> bool:
        return (
            self.rows > 0
            and self.missing_values == 0
            and self.duplicate_rows == 0
            and self.non_positive_prices == 0
            and self.high_below_low == 0
            and self.missing_volume == 0
        )

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([self.__dict__ | {"ok": self.ok}])


def normalize_intraday(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(col[0]).lower().replace(" ", "_") for col in frame.columns]
    else:
        frame.columns = [str(col).lower().replace(" ", "_") for col in frame.columns]
    frame = frame.reset_index()
    timestamp_col = "datetime" if "datetime" in frame.columns else frame.columns[0]
    frame = frame.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
    frame["ticker"] = ticker.upper()
    timestamps = pd.to_datetime(frame[timestamp_col])
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize("UTC")
    frame["timestamp"] = timestamps.dt.tz_convert("US/Eastern").dt.tz_localize(None)
    return frame[BAR_COLUMNS].sort_values(["ticker", "timestamp"]).reset_index(drop=True)


def clean_intraday_bars(frame: pd.DataFrame, regular_hours_only: bool = True) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned["ticker"] = cleaned["ticker"].astype(str).str.upper().str.strip()
    cleaned["timestamp"] = pd.to_datetime(cleaned["timestamp"])
    for column in ["open", "high", "low", "close", "volume"]:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned = cleaned.drop_duplicates(["ticker", "timestamp"], keep="last")
    if regular_hours_only:
        times = cleaned["timestamp"].dt.time
        cleaned = cleaned[(times >= pd.Timestamp("09:30").time()) & (times <= pd.Timestamp("16:00").time())]
    cleaned = cleaned.sort_values(["ticker", "timestamp"]).reset_index(drop=True)
    return cleaned[BAR_COLUMNS]


def validate_intraday_bars(frame: pd.DataFrame) -> ValidationReport:
    price_cols = ["open", "high", "low", "close"]
    return ValidationReport(
        rows=len(frame),
        missing_values=int(frame[BAR_COLUMNS].isna().sum().sum()),
        duplicate_rows=int(frame.duplicated(["ticker", "timestamp"]).sum()),
        non_positive_prices=int((frame[price_cols] <= 0).any(axis=1).sum()),
        high_below_low=int((frame["high"] < frame["low"]).sum()),
        missing_volume=int(frame["volume"].isna().sum()),
    )


def training_universe(config: QuantConfig, target_ticker: str = "SPCX") -> tuple[str, ...]:
    ordered: list[str] = []
    for ticker in [*config.watchlist, target_ticker, "QQQ", "SPY"]:
        clean = str(ticker).upper().strip()
        if clean and clean not in ordered:
            ordered.append(clean)
    return tuple(ordered)


def ticker_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "start", "end", "trading_days", "rows"])
    temp = frame.copy()
    temp["timestamp"] = pd.to_datetime(temp["timestamp"])
    temp["trading_date"] = temp["timestamp"].dt.normalize()
    return (
        temp.groupby("ticker", as_index=False)
        .agg(start=("timestamp", "min"), end=("timestamp", "max"), trading_days=("trading_date", "nunique"), rows=("timestamp", "count"))
        .sort_values("ticker")
    )


def download_intraday_data(config: QuantConfig, target_ticker: str = "SPCX") -> pd.DataFrame:
    ensure_directories(config)
    frames: list[pd.DataFrame] = []
    reports: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    universe = training_universe(config, target_ticker)
    for index, ticker in enumerate(universe):
        if index > 0:
            base_delay = max(float(config.data.request_delay_seconds), 0.0)
            jitter = max(float(config.data.request_delay_jitter_seconds), 0.0)
            delay = base_delay + random.uniform(0.0, jitter)
            LOGGER.info("Sleeping %.2f seconds before downloading %s to reduce Yahoo request pressure", delay, ticker)
            time.sleep(delay)
        try:
            raw = yf.download(ticker, period=config.data.period, interval=config.data.interval, auto_adjust=False, progress=False)
        except Exception as exc:
            LOGGER.warning("Failed to download %s: %s", ticker, exc)
            failures.append({"ticker": ticker, "status": "failed", "reason": str(exc)})
            continue
        if raw.empty:
            LOGGER.warning("No rows returned for %s", ticker)
            failures.append({"ticker": ticker, "status": "failed", "reason": "empty"})
            continue
        normalized = normalize_intraday(raw, ticker)
        save_frame(normalized, config.raw_dir / f"{ticker}_1m.csv")
        cleaned = clean_intraday_bars(normalized)
        report = validate_intraday_bars(cleaned)
        save_frame(cleaned, config.processed_dir / f"{ticker}_1m.parquet")
        reports.append(report.to_frame().assign(ticker=ticker))
        frames.append(cleaned)
    if reports:
        save_frame(pd.concat(reports, ignore_index=True), config.reports_dir / "data_validation.csv")
    if frames:
        coverage = ticker_coverage(pd.concat(frames, ignore_index=True))
        save_frame(coverage, config.reports_dir / "ticker_coverage.csv")
    if failures:
        save_frame(pd.DataFrame(failures), config.reports_dir / "download_failures.csv")
    if not frames:
        raise ValueError("No intraday data downloaded")
    return pd.concat(frames, ignore_index=True).sort_values(["ticker", "timestamp"]).reset_index(drop=True)


def load_processed_bars(config: QuantConfig) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in sorted(config.processed_dir.glob("*_1m.parquet"))]
    if not frames:
        raise FileNotFoundError("Run `python -m quant.cli download-data` before loading processed bars")
    frame = pd.concat(frames, ignore_index=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return clean_intraday_bars(frame)
