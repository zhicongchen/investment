from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, Holding, PriceBar

BENCHMARK_TICKER = "SPY"
TRADING_DAYS = 252


def close_value(price: PriceBar) -> Optional[float]:
    return price.adjusted_close if price.adjusted_close is not None else price.close


def prices_for(db: Session, ticker: str) -> list[PriceBar]:
    return db.scalars(
        select(PriceBar).where(PriceBar.ticker == ticker.upper()).order_by(PriceBar.date.asc())
    ).all()


def simple_moving_average(values: list[Optional[float]], window: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    clean_window: list[float] = []
    for value in values:
        if value is not None:
            clean_window.append(value)
        if len(clean_window) >= window:
            result.append(sum(clean_window[-window:]) / window)
        else:
            result.append(None)
    return result


def exponential_moving_average(values: list[Optional[float]], span: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    multiplier = 2 / (span + 1)
    previous: Optional[float] = None
    for value in values:
        if value is None:
            result.append(previous)
            continue
        previous = value if previous is None else (value - previous) * multiplier + previous
        result.append(previous)
    return result


def rsi(values: list[Optional[float]], period: int = 14) -> list[Optional[float]]:
    result: list[Optional[float]] = [None] * len(values)
    closes = [value for value in values]
    gains: list[float] = []
    losses: list[float] = []

    for index in range(1, len(closes)):
        if closes[index] is None or closes[index - 1] is None:
            gains.append(0)
            losses.append(0)
            continue
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    if len(gains) < period:
        return result

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result[period] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

    for index in range(period + 1, len(values)):
        gain = gains[index - 1]
        loss = losses[index - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[index] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return result


def bollinger(values: list[Optional[float]], window: int = 20, std_count: int = 2) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    mids: list[Optional[float]] = []
    uppers: list[Optional[float]] = []
    lowers: list[Optional[float]] = []
    clean_window: list[float] = []
    for value in values:
        if value is not None:
            clean_window.append(value)
        if len(clean_window) >= window:
            sample = clean_window[-window:]
            mid = sum(sample) / window
            variance = sum((point - mid) ** 2 for point in sample) / window
            std = math.sqrt(variance)
            mids.append(mid)
            uppers.append(mid + std_count * std)
            lowers.append(mid - std_count * std)
        else:
            mids.append(None)
            uppers.append(None)
            lowers.append(None)
    return mids, uppers, lowers


def enrich_price_points(prices: list[PriceBar]) -> list[dict]:
    closes = [close_value(price) for price in prices]
    ma20 = simple_moving_average(closes, 20)
    ma50 = simple_moving_average(closes, 50)
    ma200 = simple_moving_average(closes, 200)
    rsi_values = rsi(closes)
    ema12 = exponential_moving_average(closes, 12)
    ema26 = exponential_moving_average(closes, 26)
    macd = [
        fast - slow if fast is not None and slow is not None else None
        for fast, slow in zip(ema12, ema26)
    ]
    macd_signal = exponential_moving_average(macd, 9)
    macd_hist = [
        value - signal if value is not None and signal is not None else None
        for value, signal in zip(macd, macd_signal)
    ]
    bb_mid, bb_upper, bb_lower = bollinger(closes)

    running_high: Optional[float] = None
    points: list[dict] = []
    for index, price in enumerate(prices):
        close = closes[index]
        if close is not None:
            running_high = close if running_high is None else max(running_high, close)
        drawdown = (close - running_high) / running_high if close is not None and running_high else None
        points.append(
            {
                "date": price.date,
                "close": price.close,
                "adjusted_close": price.adjusted_close,
                "volume": price.volume,
                "ma20": ma20[index],
                "ma50": ma50[index],
                "ma200": ma200[index],
                "rsi": rsi_values[index],
                "macd": macd[index],
                "macd_signal": macd_signal[index],
                "macd_histogram": macd_hist[index],
                "bb_mid": bb_mid[index],
                "bb_upper": bb_upper[index],
                "bb_lower": bb_lower[index],
                "drawdown": drawdown,
            }
        )
    return points


def daily_returns(prices: list[PriceBar]) -> dict[date, float]:
    returns: dict[date, float] = {}
    previous: Optional[float] = None
    for price in prices:
        close = close_value(price)
        if close is not None and previous:
            returns[price.date] = close / previous - 1
        if close is not None:
            previous = close
    return returns


def cumulative_series(prices: list[PriceBar]) -> dict[date, float]:
    series: dict[date, float] = {}
    start: Optional[float] = None
    for price in prices:
        close = close_value(price)
        if close is None:
            continue
        if start is None:
            start = close
        series[price.date] = close / start - 1
    return series


def annualized_volatility(prices: list[PriceBar]) -> Optional[float]:
    returns = list(daily_returns(prices).values())
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def max_drawdown(prices: list[PriceBar]) -> Optional[float]:
    peak: Optional[float] = None
    worst = 0.0
    found = False
    for price in prices:
        close = close_value(price)
        if close is None:
            continue
        found = True
        peak = close if peak is None else max(peak, close)
        if peak:
            worst = min(worst, close / peak - 1)
    return worst if found else None


def ytd_return(prices: list[PriceBar]) -> Optional[float]:
    valid = [(price.date, close_value(price)) for price in prices if close_value(price) is not None]
    if not valid:
        return None
    current_year = valid[-1][0].year
    year_values = [(day, close) for day, close in valid if day.year == current_year]
    if not year_values or year_values[0][1] in (None, 0):
        return None
    return valid[-1][1] / year_values[0][1] - 1


def summary_metrics(prices: list[PriceBar], benchmark_prices: list[PriceBar]) -> dict:
    valid = [(price.date, close_value(price)) for price in prices if close_value(price) is not None]
    latest = valid[-1][1] if valid else None
    latest_date = valid[-1][0] if valid else None
    recent = valid[-252:] if len(valid) >= 252 else valid
    total = valid[-1][1] / valid[0][1] - 1 if len(valid) >= 2 and valid[0][1] else None

    benchmark_return = None
    if benchmark_prices and valid:
        benchmark_series = cumulative_series(benchmark_prices)
        matching = [(day, value) for day, value in benchmark_series.items() if day >= valid[0][0]]
        if matching:
            benchmark_return = matching[-1][1]

    return {
        "latest_price": latest,
        "latest_price_date": latest_date,
        "ytd_return": ytd_return(prices),
        "high_52w": max((value for _, value in recent), default=None),
        "low_52w": min((value for _, value in recent), default=None),
        "annualized_volatility": annualized_volatility(prices),
        "max_drawdown": max_drawdown(prices),
        "total_return": total,
        "benchmark_return": benchmark_return,
        "relative_return": total - benchmark_return if total is not None and benchmark_return is not None else None,
    }


def correlation_matrix(price_map: dict[str, list[PriceBar]]) -> list[dict]:
    returns_by_ticker = {ticker: daily_returns(prices) for ticker, prices in price_map.items()}
    tickers = sorted(returns_by_ticker)
    rows: list[dict] = []
    for left in tickers:
        row = {"ticker": left}
        for right in tickers:
            common_dates = sorted(set(returns_by_ticker[left]) & set(returns_by_ticker[right]))
            left_values = [returns_by_ticker[left][day] for day in common_dates]
            right_values = [returns_by_ticker[right][day] for day in common_dates]
            row[right] = _correlation(left_values, right_values)
        rows.append(row)
    return rows


def _correlation(left: list[float], right: list[float]) -> Optional[float]:
    if len(left) < 2 or len(right) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_den = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_den = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    if left_den == 0 or right_den == 0:
        return None
    return numerator / (left_den * right_den)


def market_comparison(db: Session) -> dict:
    companies = db.scalars(select(Company).where(Company.active.is_(True)).order_by(Company.ticker.asc())).all()
    tickers = [company.ticker for company in companies if company.ticker != BENCHMARK_TICKER]
    price_map = {ticker: prices_for(db, ticker) for ticker in tickers}
    benchmark_prices = prices_for(db, BENCHMARK_TICKER)
    benchmark = cumulative_series(benchmark_prices)
    series_by_ticker = {ticker: cumulative_series(prices) for ticker, prices in price_map.items()}
    all_dates = sorted(set().union(*(set(series) for series in series_by_ticker.values()), set(benchmark)))
    cumulative = []
    for day in all_dates:
        row = {"date": day}
        for ticker, series in series_by_ticker.items():
            if day in series:
                row[ticker] = series[day]
        if day in benchmark:
            row[BENCHMARK_TICKER] = benchmark[day]
        cumulative.append(row)

    return {
        "benchmark": BENCHMARK_TICKER,
        "tickers": tickers,
        "cumulative_returns": cumulative,
        "correlation": correlation_matrix(price_map),
    }


def portfolio_analytics(db: Session) -> dict:
    holdings = db.scalars(select(Holding).order_by(Holding.ticker.asc())).all()
    price_map = {holding.ticker: prices_for(db, holding.ticker) for holding in holdings}
    benchmark = cumulative_series(prices_for(db, BENCHMARK_TICKER))
    values_by_date: dict[date, float] = defaultdict(float)
    for holding in holdings:
        for price in price_map.get(holding.ticker, []):
            close = close_value(price)
            if close is not None:
                values_by_date[price.date] += holding.shares * close

    ordered = [(day, value) for day, value in sorted(values_by_date.items()) if value > 0]
    portfolio_series = []
    returns: list[float] = []
    previous: Optional[float] = None
    start = ordered[0][1] if ordered else None
    for day, value in ordered:
        if previous:
            returns.append(value / previous - 1)
        previous = value
        portfolio_series.append(
            {
                "date": day,
                "portfolio": value / start - 1 if start else None,
                BENCHMARK_TICKER: benchmark.get(day),
            }
        )

    volatility = None
    sharpe = None
    total_return = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        volatility = math.sqrt(variance) * math.sqrt(TRADING_DAYS)
        sharpe = (mean * TRADING_DAYS) / volatility if volatility else None
    if len(ordered) >= 2 and ordered[0][1]:
        total_return = ordered[-1][1] / ordered[0][1] - 1

    current_value = ordered[-1][1] if ordered else 0
    allocation = []
    for holding in holdings:
        latest = next((close_value(price) for price in reversed(price_map.get(holding.ticker, [])) if close_value(price) is not None), None)
        market_value = holding.shares * latest if latest is not None else 0
        current_allocation = market_value / current_value if current_value else 0
        allocation.append(
            {
                "ticker": holding.ticker,
                "current_allocation": current_allocation,
                "target_allocation": holding.target_allocation,
                "drift": current_allocation - holding.target_allocation,
            }
        )

    return {
        "benchmark": BENCHMARK_TICKER,
        "total_value": current_value,
        "total_return": total_return,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "series": portfolio_series,
        "allocation": allocation,
    }


def import_demo_csvs(db: Session, demo_data_dir: Path) -> list[str]:
    imported: list[str] = []
    if not demo_data_dir.exists():
        return imported
    for path in sorted(demo_data_dir.glob("*.csv")):
        ticker = path.stem.upper()
        company = db.get(Company, ticker)
        if company is None:
            company = Company(ticker=ticker, name=ticker, active=ticker != BENCHMARK_TICKER)
            db.add(company)
            db.flush()
        elif ticker == BENCHMARK_TICKER:
            company.active = False
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw_date = row.get("Date") or row.get("") or next(iter(row.values()))
                try:
                    day = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    continue
                existing = db.scalars(
                    select(PriceBar).where(PriceBar.ticker == ticker, PriceBar.date == day)
                ).first()
                if existing is None:
                    existing = PriceBar(ticker=ticker, date=day)
                    db.add(existing)
                existing.open = _float(row.get("Open"))
                existing.high = _float(row.get("High"))
                existing.low = _float(row.get("Low"))
                existing.close = _float(row.get("Close"))
                existing.volume = _float(row.get("Volume"))
                existing.adjusted_close = _float(row.get("Adj Close") or row.get("Adj_Close"))
        imported.append(ticker)
    db.commit()
    return imported


def _float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None
