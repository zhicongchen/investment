from __future__ import annotations

from datetime import date
from datetime import datetime
import json
from math import isfinite
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yfinance as yf

from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


class YFinanceProvider(MarketDataProvider):
    def get_company_profile(self, ticker: str) -> CompanyProfile:
        symbol = ticker.upper().strip()
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            info = {}
        chart_meta = _chart_meta(symbol)
        name = info.get("shortName") or info.get("longName") or chart_meta.get("shortName") or symbol
        return CompanyProfile(
            ticker=symbol,
            name=name,
            sector=info.get("sector") or "",
            exchange=info.get("exchange") or chart_meta.get("exchangeName") or "",
        )

    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        symbol = ticker.upper().strip()
        try:
            history = yf.Ticker(symbol).history(period=period, auto_adjust=False)
        except Exception:
            history = None
        if history is not None and not history.empty:
            bars: list[PriceBarData] = []
            for idx, row in history.iterrows():
                bars.append(
                    PriceBarData(
                        date=idx.date(),
                        open=_number(row.get("Open")),
                        high=_number(row.get("High")),
                        low=_number(row.get("Low")),
                        close=_number(row.get("Close")),
                        volume=_number(row.get("Volume")),
                        adjusted_close=_number(row.get("Adj Close")),
                    )
                )
            return bars
        return _chart_history(symbol, period)

    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        symbol = ticker.upper().strip()
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            info = {}
        return FundamentalsData(
            date=date.today(),
            pe=_number(info.get("trailingPE")),
            forward_pe=_number(info.get("forwardPE")),
            eps_growth=_number(info.get("earningsGrowth")),
            revenue_growth=_number(info.get("revenueGrowth")),
            debt_to_equity=_number(info.get("debtToEquity")),
            free_cash_flow=_number(info.get("freeCashflow")),
            dividend_yield=_number(info.get("dividendYield")),
        )


def _chart_meta(symbol: str) -> dict[str, Any]:
    try:
        result = _chart_result(symbol, "5d")
    except Exception:
        return {}
    return result.get("meta") or {}


def _chart_history(symbol: str, period: str) -> list[PriceBarData]:
    result = _chart_result(symbol, period)
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]
    bars: list[PriceBarData] = []
    for index, timestamp in enumerate(timestamps):
        bars.append(
            PriceBarData(
                date=datetime.utcfromtimestamp(timestamp).date(),
                open=_number((quote.get("open") or [None])[index]),
                high=_number((quote.get("high") or [None])[index]),
                low=_number((quote.get("low") or [None])[index]),
                close=_number((quote.get("close") or [None])[index]),
                volume=_number((quote.get("volume") or [None])[index]),
                adjusted_close=_number((adjusted.get("adjclose") or [None])[index]),
            )
        )
    bars = [bar for bar in bars if bar.close is not None or bar.adjusted_close is not None]
    if not bars:
        raise ValueError(f"No price history returned for {symbol}")
    return bars


def _chart_result(symbol: str, period: str) -> dict[str, Any]:
    query = urlencode({"range": period, "interval": "1d", "events": "history"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise ValueError(error)
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"No chart data returned for {symbol}")
    return results[0]
