from __future__ import annotations

import numpy as np
import pandas as pd

from quant.config import QuantConfig, ensure_directories
from quant.io import save_frame

OPEN_MINUTE = 9 * 60 + 30
CLOSE_MINUTE = 16 * 60


def trading_date(timestamp: pd.Series) -> pd.Series:
    return pd.to_datetime(timestamp).dt.normalize()


def ticker_encoder(tickers: pd.Series | list[str] | tuple[str, ...]) -> dict[str, int]:
    unique = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    return {"__UNKNOWN__": 0, **{ticker: idx + 1 for idx, ticker in enumerate(unique)}}


def apply_ticker_encoder(frame: pd.DataFrame, encoder: dict[str, int] | None = None) -> pd.DataFrame:
    result = frame.copy()
    if encoder is None:
        encoder = ticker_encoder(result["ticker"])
    result["ticker_code"] = result["ticker"].astype(str).str.upper().map(encoder).fillna(encoder.get("__UNKNOWN__", 0)).astype(int)
    return result


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(group: pd.DataFrame, window: int = 14) -> pd.Series:
    previous_close = group["close"].shift(1)
    true_range = pd.concat(
        [
            group["high"] - group["low"],
            (group["high"] - previous_close).abs(),
            (group["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window, min_periods=window).mean()


def _base_features_for_day(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("timestamp").copy()
    close = g["close"]
    ret_1m = close.pct_change()
    for window in (1, 2, 3, 5, 10, 15, 30, 60):
        g[f"ret_{window}m"] = close.pct_change(window)
        g[f"return_{window}m"] = g[f"ret_{window}m"]
    g["log_ret_1m"] = np.log(close / close.shift(1))
    g["log_return_1m"] = g["log_ret_1m"]
    g["return_since_open"] = close / close.iloc[0] - 1
    g["cumulative_return_from_open"] = (1 + ret_1m.fillna(0)).cumprod() - 1
    g["overnight_gap"] = np.nan

    typical_dollar = ((g["high"] + g["low"] + g["close"]) / 3) * g["volume"]
    cumulative_volume = g["volume"].cumsum().replace(0, np.nan)
    vwap = typical_dollar.cumsum() / cumulative_volume
    g["vwap_intraday"] = vwap
    g["price_vs_vwap"] = close / vwap - 1

    for window in (5, 15, 30, 60):
        rolling_mean = close.rolling(window, min_periods=window).mean()
        g[f"ma_{window}_gap"] = close / rolling_mean - 1
        g[f"ma_{window}m_gap"] = g[f"ma_{window}_gap"]
        g[f"rolling_vol_{window}m"] = ret_1m.rolling(window, min_periods=window).std()
        g[f"realized_vol_{window}"] = g[f"rolling_vol_{window}m"]
    for window in (5, 10, 15, 20, 30, 60):
        ema = close.ewm(span=window, adjust=False, min_periods=window).mean()
        g[f"ema_{window}"] = ema
        if window in (5, 15, 30):
            g[f"ema_{window}_gap"] = close / ema - 1
    g["price_vs_ema_5"] = close / g["ema_5"] - 1
    g["price_vs_ema_20"] = close / g["ema_20"] - 1
    g["price_vs_ema_60"] = close / g["ema_60"] - 1
    g["ema_5_vs_ema_20"] = g["ema_5"] / g["ema_20"] - 1
    g["ema_20_vs_ema_60"] = g["ema_20"] / g["ema_60"] - 1
    for window in (20, 60):
        mean = close.rolling(window, min_periods=window).mean()
        std = close.rolling(window, min_periods=window).std().replace(0, np.nan)
        g[f"rolling_zscore_price_{window}"] = (close - mean) / std

    spread = (g["high"] - g["low"]).replace(0, np.nan)
    g["ATR_14"] = _atr(g, 14)
    g["atr_14"] = g["ATR_14"]
    g["high_low_range"] = spread / close
    g["current_bar_range"] = g["high_low_range"]
    g["current_bar_body"] = (g["close"] - g["open"]).abs() / close
    g["upper_wick_ratio"] = (g["high"] - g[["open", "close"]].max(axis=1)) / spread
    g["lower_wick_ratio"] = (g[["open", "close"]].min(axis=1) - g["low"]) / spread
    g["close_position"] = (close - g["low"]) / spread
    for window in (5, 15, 60):
        g[f"rolling_high_low_range_{window}"] = g["high_low_range"].rolling(window, min_periods=window).mean()
    day_high_so_far = g["high"].cummax()
    day_low_so_far = g["low"].cummin()
    day_range = (day_high_so_far - day_low_so_far).replace(0, np.nan)
    g["distance_to_intraday_high"] = close / day_high_so_far - 1
    g["distance_to_intraday_low"] = close / day_low_so_far - 1
    g["intraday_range_position"] = (close - day_low_so_far) / day_range

    for window in (5, 20, 60):
        g[f"volume_ratio_{window}m"] = g["volume"] / g["volume"].rolling(window, min_periods=window).mean()
    g["volume_change_1m"] = g["volume"].pct_change()
    g["volume_ma_5"] = g["volume"].rolling(5, min_periods=5).mean()
    g["volume_ma_20"] = g["volume"].rolling(20, min_periods=20).mean()
    vol_mean_20 = g["volume"].rolling(20, min_periods=20).mean()
    vol_std_20 = g["volume"].rolling(20, min_periods=20).std()
    g["volume_zscore_20m"] = (g["volume"] - vol_mean_20) / vol_std_20.replace(0, np.nan)
    g["volume_zscore_20"] = g["volume_zscore_20m"]
    vol_mean_60 = g["volume"].rolling(60, min_periods=60).mean()
    vol_std_60 = g["volume"].rolling(60, min_periods=60).std()
    g["volume_zscore_60"] = (g["volume"] - vol_mean_60) / vol_std_60.replace(0, np.nan)
    g["dollar_volume"] = close * g["volume"]
    g["rolling_dollar_volume_20m"] = g["dollar_volume"].rolling(20, min_periods=20).mean()
    dv_mean = g["dollar_volume"].rolling(20, min_periods=20).mean()
    dv_std = g["dollar_volume"].rolling(20, min_periods=20).std()
    g["dollar_volume_zscore"] = (g["dollar_volume"] - dv_mean) / dv_std.replace(0, np.nan)

    g["RSI_14"] = _rsi(close, 14)
    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    g["MACD"] = ema_12 - ema_26
    g["MACD_signal"] = g["MACD"].ewm(span=9, adjust=False, min_periods=9).mean()
    g["MACD_histogram"] = g["MACD"] - g["MACD_signal"]

    minutes = g["timestamp"].dt.hour * 60 + g["timestamp"].dt.minute
    g["minute_of_day"] = minutes
    g["minute_of_day_sin"] = np.sin(2 * np.pi * (minutes - OPEN_MINUTE) / 390)
    g["minute_of_day_cos"] = np.cos(2 * np.pi * (minutes - OPEN_MINUTE) / 390)
    g["minutes_from_open"] = minutes - OPEN_MINUTE
    g["minutes_to_close"] = CLOSE_MINUTE - minutes
    g["is_first_15m"] = (g["minutes_from_open"] < 15).astype(int)
    g["is_opening_15m"] = g["is_first_15m"]
    g["is_first_30m"] = (g["minutes_from_open"] < 30).astype(int)
    g["is_opening_30m"] = g["is_first_30m"]
    g["is_lunch_period"] = ((minutes >= 12 * 60) & (minutes < 13 * 60)).astype(int)
    g["is_last_30m"] = (g["minutes_to_close"] < 30).astype(int)
    g["is_closing_30m"] = g["is_last_30m"]
    g["is_closing_15m"] = (g["minutes_to_close"] < 15).astype(int)
    g["hour"] = g["timestamp"].dt.hour
    g["minute"] = g["timestamp"].dt.minute
    return g


def _add_market_features(features: pd.DataFrame) -> pd.DataFrame:
    result = features.copy()
    market_map = {"QQQ": "qqq", "SPY": "spy"}
    for ticker, prefix in market_map.items():
        market = result.loc[result["ticker"] == ticker, ["timestamp", "ret_1m", "ret_5m", "ret_15m", "ret_30m", "ret_60m", "rolling_vol_30m"]].copy()
        market = market.rename(
            columns={
                "ret_1m": f"{prefix}_return_1m",
                "ret_5m": f"{prefix}_ret_5m",
                "ret_5m": f"{prefix}_return_5m",
                "ret_15m": f"{prefix}_ret_15m",
                "ret_15m": f"{prefix}_return_15m",
                "ret_30m": f"{prefix}_return_30m",
                "ret_60m": f"{prefix}_ret_60m",
                "ret_60m": f"{prefix}_return_60m",
                "rolling_vol_30m": f"{prefix}_vol_30m",
            }
        )
        result = result.merge(market, on="timestamp", how="left")
        result[f"{prefix}_ret_5m"] = result[f"{prefix}_return_5m"]
        result[f"{prefix}_ret_15m"] = result[f"{prefix}_return_15m"]
        result[f"{prefix}_ret_60m"] = result[f"{prefix}_return_60m"]
    result["ticker_ret_5m_minus_qqq"] = result["ret_5m"] - result["qqq_ret_5m"]
    result["ticker_ret_15m_minus_qqq"] = result["ret_15m"] - result["qqq_ret_15m"]
    result["ticker_ret_60m_minus_qqq"] = result["ret_60m"] - result["qqq_ret_60m"]
    result["ticker_ret_15m_minus_spy"] = result["ret_15m"] - result["spy_ret_15m"]
    result["relative_return_vs_qqq_1m"] = result["ret_1m"] - result["qqq_return_1m"]
    result["relative_return_vs_qqq_5m"] = result["ret_5m"] - result["qqq_return_5m"]
    result["relative_return_vs_qqq_15m"] = result["ret_15m"] - result["qqq_return_15m"]
    result["relative_return_vs_qqq_60m"] = result["ret_60m"] - result["qqq_return_60m"]
    qqq = result.loc[result["ticker"] == "QQQ", ["timestamp", "ret_1m"]].rename(columns={"ret_1m": "qqq_ret_1m_for_beta"})
    result = result.merge(qqq, on="timestamp", how="left")
    pieces = []
    for _, group in result.sort_values(["ticker", "timestamp"]).groupby("ticker", sort=False):
        g = group.copy()
        cov = g["ret_1m"].rolling(60, min_periods=60).cov(g["qqq_ret_1m_for_beta"])
        var = g["qqq_ret_1m_for_beta"].rolling(60, min_periods=60).var()
        g["rolling_beta_vs_qqq_60"] = cov / var.replace(0, np.nan)
        g["rolling_corr_vs_qqq_60"] = g["ret_1m"].rolling(60, min_periods=60).corr(g["qqq_ret_1m_for_beta"])
        pieces.append(g)
    result = pd.concat(pieces, ignore_index=True).drop(columns=["qqq_ret_1m_for_beta"])
    return result


def _add_cross_section_features(features: pd.DataFrame) -> pd.DataFrame:
    result = features.copy()
    grouped = result.groupby("timestamp")
    result["cross_section_mean_return_1m"] = grouped["ret_1m"].transform("mean")
    result["cross_section_median_return_1m"] = grouped["ret_1m"].transform("median")
    result["cross_section_std_return_1m"] = grouped["ret_1m"].transform("std")
    result["cross_section_mean_return_5m"] = grouped["ret_5m"].transform("mean")
    result["cross_section_rank_return_5m"] = grouped["ret_5m"].rank(pct=True)
    result["cross_section_rank_return_15m"] = grouped["ret_15m"].rank(pct=True)
    result["cross_section_rank_volume_zscore"] = grouped["volume_zscore_20m"].rank(pct=True)
    result["advance_ratio"] = grouped["ret_1m"].transform(lambda s: float((s > 0).mean()))
    return result


def _add_same_minute_relative_volume(features: pd.DataFrame) -> pd.DataFrame:
    result = features.copy()
    result["trading_date"] = result["timestamp"].dt.normalize()
    result["minute_key"] = result["timestamp"].dt.hour * 60 + result["timestamp"].dt.minute
    pieces = []
    for _, group in result.sort_values(["ticker", "trading_date", "minute_key"]).groupby(["ticker", "minute_key"], sort=False):
        g = group.copy()
        historical_mean = g["volume"].expanding(min_periods=1).mean().shift(1)
        g["relative_volume_same_minute"] = g["volume"] / historical_mean.replace(0, np.nan)
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True).drop(columns=["trading_date", "minute_key"])


def add_minute_features(bars: pd.DataFrame, encoder: dict[str, int] | None = None) -> pd.DataFrame:
    frame = bars.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["trading_date"] = trading_date(frame["timestamp"])
    pieces = []
    for _, group in frame.sort_values(["ticker", "timestamp"]).groupby(["ticker", "trading_date"], sort=False):
        pieces.append(_base_features_for_day(group))
    if not pieces:
        return pd.DataFrame()
    features = pd.concat(pieces, ignore_index=True).drop(columns=["trading_date"])
    features = _add_market_features(features)
    features = _add_cross_section_features(features)
    features = _add_same_minute_relative_volume(features)
    features = apply_ticker_encoder(features, encoder)
    return features.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


def save_features(features: pd.DataFrame, config: QuantConfig) -> None:
    ensure_directories(config)
    save_frame(features, config.processed_dir / "features.parquet")
