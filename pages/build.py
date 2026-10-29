"""Fetch the online dashboard's data from Yahoo Finance and write it as static JSON.

Run once a day after the US close by .github/workflows/pages.yml:

    python -m pages.build --out frontend/public/data

Writes market.json (companies, fundamentals), prices/<TICKER>.json (daily bars) and
options-scan.json (the put scanner run over the watchlist with options/config.yaml).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from options.config import load_config
from options.report import to_dict
from options.scanner import ET, run_scan
from options.yahoo import YahooClient, bars_from_history

WATCHLIST_PATH = Path(__file__).resolve().parent / "watchlist.yaml"
BENCHMARK = "SPY"
HISTORY_PERIOD = "5y"
NO_GREEKS_WARNING = "Moomoo returned option quotes without greeks"


def load_watchlist(path: Path = WATCHLIST_PATH) -> list[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tickers = [str(item).strip().upper() for item in raw.get("tickers") or [] if str(item).strip()]
    return list(dict.fromkeys(tickers))


def build(out_dir: Path, tickers: list[str], ticker_factory: Callable[[str], Any], now: datetime, pause_seconds: float = 0.5) -> dict[str, Any]:
    prices_dir = out_dir / "prices"
    prices_dir.mkdir(parents=True, exist_ok=True)
    companies: list[dict[str, str]] = []
    fundamentals: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    bars = {}

    for ticker in dict.fromkeys([*tickers, BENCHMARK]):
        try:
            handle = ticker_factory(ticker)
            history = handle.history(period=HISTORY_PERIOD, auto_adjust=False)
            rows = price_rows(history)
            if not rows:
                raise ValueError("Yahoo returned no price history")
            _write(prices_dir / f"{ticker}.json", rows)
            bars[ticker] = bars_from_history(history)
            if ticker in tickers:
                info = _info(handle)
                companies.append(company(ticker, info))
                fundamentals[ticker] = fundamentals_from_info(info, now.date())
        except Exception as exc:
            errors[ticker] = str(exc) or exc.__class__.__name__
        time.sleep(pause_seconds)

    config = load_config()
    config.watchlist.extra_tickers = sorted(set(tickers) | set(config.watchlist.extra_tickers))
    client = YahooClient(today=now.date(), bars=bars, ticker_factory=ticker_factory, pause_seconds=pause_seconds, min_dte=config.puts.min_dte)
    scan = run_scan(config, client, now=now)
    scan.positions_status = "IBKR positions are not available in the online version."
    scan.warnings = [warning for warning in scan.warnings if NO_GREEKS_WARNING not in warning]
    scan.warnings.append("Yahoo Finance has no greeks or IV rank: delta is estimated with Black-Scholes (marked *).")
    if client.last_price_fallbacks:
        scan.warnings.append(
            f"{client.last_price_fallbacks} option quote(s) had no bid/ask on Yahoo; their last trade price was used for both."
        )
    _write(out_dir / "options-scan.json", to_dict(scan))

    market = {
        "generated_at": now.isoformat(),
        "benchmark": BENCHMARK,
        "tickers": companies,
        "fundamentals": fundamentals,
        "errors": errors,
    }
    _write(out_dir / "market.json", market)
    return market


def price_rows(history: Any) -> list[list[Any]]:
    """[date, open, high, low, close, volume, adjusted close] per day."""
    rows: list[list[Any]] = []
    if history is None or history.empty:
        return rows
    for index, row in history.iterrows():
        values = [_round(row.get(column)) for column in ("Open", "High", "Low", "Close", "Volume", "Adj Close")]
        if values[3] is None and values[5] is None:
            continue
        rows.append([index.date().isoformat(), *values])
    return rows


def company(ticker: str, info: dict[str, Any]) -> dict[str, str]:
    return {
        "ticker": ticker,
        "name": str(info.get("shortName") or info.get("longName") or ticker),
        "sector": str(info.get("sector") or ""),
        "exchange": str(info.get("exchange") or ""),
    }


def fundamentals_from_info(info: dict[str, Any], today: date) -> dict[str, Any]:
    """Same fields as backend/app/providers/yfinance_provider.py."""
    return {
        "date": today.isoformat(),
        "pe": _number(info.get("trailingPE")),
        "forward_pe": _number(info.get("forwardPE")),
        "eps_growth": _number(info.get("earningsGrowth")),
        "revenue_growth": _number(info.get("revenueGrowth")),
        "debt_to_equity": _number(info.get("debtToEquity")),
        "free_cash_flow": _number(info.get("freeCashflow")),
        "dividend_yield": _number(info.get("dividendYield")),
    }


def _info(handle: Any) -> dict[str, Any]:
    try:
        return handle.info or {}
    except Exception:
        return {}


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round(value: Any) -> Optional[float]:
    parsed = _number(value)
    return round(parsed, 4) if parsed is not None else None


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for the JSON files")
    parser.add_argument("--watchlist", type=Path, default=WATCHLIST_PATH)
    args = parser.parse_args(argv)

    import yfinance as yf

    tickers = load_watchlist(args.watchlist)
    market = build(args.out, tickers, yf.Ticker, datetime.now(ET))
    fetched = len(market["tickers"])
    print(f"Fetched {fetched}/{len(tickers)} tickers into {args.out}")
    for ticker, error in market["errors"].items():
        print(f"  {ticker}: {error}", file=sys.stderr)
    # Publish partial data, but fail the run when Yahoo gave us nothing at all.
    return 0 if fetched else 1


if __name__ == "__main__":
    raise SystemExit(main())
