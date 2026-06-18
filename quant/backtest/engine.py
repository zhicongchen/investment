from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.config import QuantConfig, ensure_directories
from quant.io import save_frame
from quant.metrics import equity_metrics
from quant.risk.controls import RiskState, record_trade_result, update_daily_state
from quant.signals import select_portfolio


@dataclass
class Position:
    ticker: str
    entry_timestamp: pd.Timestamp
    entry_price: float
    weight: float
    probability_up: float


def _price_lookup(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.pivot(index="timestamp", columns="ticker", values="open").sort_index()


def _close_lookup(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.pivot(index="timestamp", columns="ticker", values="close").sort_index()


def _next_timestamp(index: pd.Index, timestamp: pd.Timestamp) -> pd.Timestamp | None:
    location = index.searchsorted(timestamp, side="right")
    if location >= len(index):
        return None
    return index[location]


def benchmark_buy_hold(bars: pd.DataFrame, ticker: str, timestamps: pd.Series) -> pd.DataFrame:
    ticker_bars = bars.loc[bars["ticker"] == ticker].sort_values("timestamp")
    if ticker_bars.empty:
        return pd.DataFrame()
    close = ticker_bars.set_index("timestamp")["close"].reindex(timestamps).ffill().dropna()
    if close.empty:
        return pd.DataFrame()
    return pd.DataFrame({"timestamp": close.index, "portfolio": ticker, "equity": close / close.iloc[0]})


def run_backtest(bars: pd.DataFrame, features: pd.DataFrame, signals: pd.DataFrame, config: QuantConfig) -> dict[str, pd.DataFrame]:
    ensure_directories(config)
    open_px = _price_lookup(bars)
    close_px = _close_lookup(bars)
    timestamps = pd.Index(sorted(set(signals["timestamp"]).intersection(open_px.index)))
    positions: dict[str, Position] = {}
    equity = config.portfolio.initial_cash
    risk_state = RiskState(day_start_equity=equity)
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    risk_rows: list[dict[str, object]] = []
    cost_rate = config.costs.total_rate
    for timestamp in timestamps:
        timestamp = pd.Timestamp(timestamp)
        risk_state = update_daily_state(risk_state, timestamp, equity, config)
        current_close = close_px.loc[timestamp]
        current_signals = signals.loc[signals["timestamp"] == timestamp]
        current_features = features.loc[features["timestamp"] <= timestamp]
        signal_tickers = set(current_signals.loc[current_signals["signal"] == "LONG", "ticker"])

        for ticker, position in list(positions.items()):
            age = int((timestamp - position.entry_timestamp).total_seconds() // 60)
            should_exit = age >= config.label.horizon_minutes or ticker not in signal_tickers or risk_state.trading_halted
            if should_exit and ticker in current_close and pd.notna(current_close[ticker]):
                exit_price = float(current_close[ticker]) * (1 - cost_rate)
                gross_return = exit_price / position.entry_price - 1
                trade_return = gross_return * position.weight
                equity *= 1 + trade_return
                record_trade_result(risk_state, trade_return, config)
                trade_rows.append(
                    {
                        "ticker": ticker,
                        "entry_timestamp": position.entry_timestamp,
                        "exit_timestamp": timestamp,
                        "entry_price": position.entry_price,
                        "exit_price": exit_price,
                        "weight": position.weight,
                        "trade_return": trade_return,
                        "holding_minutes": age,
                    }
                )
                del positions[ticker]

        execution_timestamp = _next_timestamp(open_px.index, timestamp)
        if execution_timestamp is not None:
            selected = select_portfolio(current_signals, config)
            available_slots = config.portfolio.max_positions - len(positions)
            selected = selected.loc[~selected["ticker"].isin(positions)].head(max(available_slots, 0))
            if not risk_state.trading_halted:
                for _, row in selected.iterrows():
                    ticker = row["ticker"]
                    if ticker in open_px.columns and pd.notna(open_px.loc[execution_timestamp, ticker]):
                        entry_price = float(open_px.loc[execution_timestamp, ticker]) * (1 + cost_rate)
                        positions[ticker] = Position(
                            ticker=ticker,
                            entry_timestamp=execution_timestamp,
                            entry_price=entry_price,
                            weight=float(row["target_weight"]),
                            probability_up=float(row["probability_up"]),
                        )

        mark_to_market = 0.0
        for ticker, position in positions.items():
            if ticker in current_close and pd.notna(current_close[ticker]):
                mark_to_market += position.weight * (float(current_close[ticker]) / position.entry_price - 1)
        marked_equity = equity * (1 + mark_to_market)
        equity_rows.append({"timestamp": timestamp, "equity": marked_equity, "open_positions": len(positions)})
        risk_rows.append({"timestamp": timestamp, "trading_halted": risk_state.trading_halted, "reason": risk_state.reason, "consecutive_losses": risk_state.consecutive_losses})

    equity_curve = pd.DataFrame(equity_rows)
    trades = pd.DataFrame(trade_rows)
    risk = pd.DataFrame(risk_rows)
    metrics = pd.DataFrame([{"portfolio": "strategy", **equity_metrics(equity_curve, trades)}])
    for benchmark in ("QQQ", "SPY"):
        bh = benchmark_buy_hold(bars, benchmark, equity_curve["timestamp"] if not equity_curve.empty else pd.Series(dtype="datetime64[ns]"))
        if not bh.empty:
            metrics = pd.concat([metrics, pd.DataFrame([{"portfolio": benchmark, **equity_metrics(bh, pd.DataFrame())}])], ignore_index=True)
    save_frame(trades, config.reports_dir / "trades.csv")
    save_frame(equity_curve, config.reports_dir / "equity_curve.csv")
    save_frame(risk, config.reports_dir / "risk_status.csv")
    save_frame(metrics, config.reports_dir / "metrics.csv")
    return {"trades": trades, "equity": equity_curve, "risk": risk, "metrics": metrics}
