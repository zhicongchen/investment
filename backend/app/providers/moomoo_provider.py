from __future__ import annotations

import re
from datetime import date, timedelta

from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData
from shared.moomoo import MoomooClient, MoomooError

_PERIOD_DAYS = {"d": 1, "wk": 7, "mo": 31, "y": 366}


class MoomooProvider(MarketDataProvider):
    """Prices, name/exchange, trailing PE and dividend yield from Moomoo OpenD.

    Moomoo has no sector, forward PE, growth, leverage or free-cash-flow data; the factory pairs
    this provider with yfinance or FMP to fill those in.
    """

    def __init__(self, client: MoomooClient) -> None:
        self.client = client

    def get_company_profile(self, ticker: str) -> CompanyProfile:
        symbol = ticker.upper().strip()
        try:
            info = self.client.stock_basic_info([symbol]).get(symbol, {})
        except MoomooError:
            info = {}
        return CompanyProfile(ticker=symbol, name=info.get("name") or symbol, exchange=info.get("exchange") or "")

    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        symbol = ticker.upper().strip()
        start = date.today() - timedelta(days=period_days(period))
        raw = {bar.date: bar for bar in self.client.daily_bars(symbol, start, adjusted=False)}
        adjusted = self.client.daily_bars(symbol, start, adjusted=True)
        if not adjusted and not raw:
            raise ValueError(f"No price history returned for {symbol}")
        bars: list[PriceBarData] = []
        for adj in adjusted or raw.values():
            bar = raw.get(adj.date, adj)
            bars.append(
                PriceBarData(
                    date=adj.date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    adjusted_close=adj.close,
                )
            )
        return bars

    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        symbol = ticker.upper().strip()
        snapshot = self.client.stock_snapshots([symbol]).get(symbol, {})
        return FundamentalsData(
            date=date.today(),
            pe=snapshot.get("pe_ttm"),
            dividend_yield=snapshot.get("dividend_yield_ttm"),
        )

    def close(self) -> None:
        self.client.close()


def period_days(period: str) -> int:
    """yfinance-style period ('5y', '6mo', '30d', '2wk', 'max') to calendar days."""
    if period == "max":
        return 366 * 20
    match = re.fullmatch(r"(\d+)(d|wk|mo|y)", period.strip())
    if not match:
        raise ValueError(f"Unsupported period: {period!r}")
    return int(match.group(1)) * _PERIOD_DAYS[match.group(2)]
