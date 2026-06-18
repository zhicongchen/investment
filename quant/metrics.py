from __future__ import annotations

import numpy as np
import pandas as pd


def equity_metrics(equity_curve: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float]:
    equity = equity_curve.set_index("timestamp")["equity"]
    returns = equity.pct_change().dropna()
    minutes_per_year = 252 * 390
    total_return = equity.iloc[-1] / equity.iloc[0] - 1 if len(equity) else 0.0
    years = max(len(returns) / minutes_per_year, 1 / minutes_per_year)
    cagr = np.nan if len(returns) < 390 else (1 + total_return) ** (1 / years) - 1
    sharpe = np.sqrt(minutes_per_year) * returns.mean() / returns.std() if returns.std() else np.nan
    downside = returns[returns < 0]
    sortino = np.sqrt(minutes_per_year) * returns.mean() / downside.std() if downside.std() else np.nan
    drawdown = equity / equity.cummax() - 1
    max_drawdown = drawdown.min() if len(drawdown) else 0.0
    trade_returns = trades["trade_return"] if "trade_return" in trades else pd.Series(dtype=float)
    wins = trade_returns[trade_returns > 0]
    losses = trade_returns[trade_returns < 0]
    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": cagr / abs(max_drawdown) if max_drawdown < 0 else np.nan,
        "win_rate": float((trade_returns > 0).mean()) if len(trade_returns) else np.nan,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else np.nan,
        "average_trade_return": float(trade_returns.mean()) if len(trade_returns) else np.nan,
        "number_of_trades": float(len(trade_returns)),
        "average_holding_time": float(trades["holding_minutes"].mean()) if "holding_minutes" in trades and len(trades) else np.nan,
    }
