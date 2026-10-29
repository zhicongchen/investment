from __future__ import annotations

from datetime import date
from datetime import datetime
import json
from math import isfinite
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yfinance as yf

from app.providers.base import (
    STATEMENT_FIELDS,
    CompanyProfile,
    FinancialStatementData,
    FundamentalsData,
    MarketDataProvider,
    PriceBarData,
)

# Statement field -> yfinance row labels, first match wins.
_STATEMENT_ROWS: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "cost_of_revenue": ("Cost Of Revenue", "Reconciled Cost Of Revenue"),
    "gross_profit": ("Gross Profit",),
    "operating_income": ("Operating Income", "EBIT"),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "pretax_income": ("Pretax Income",),
    "income_tax": ("Tax Provision",),
    "net_income": ("Net Income Common Stockholders", "Net Income"),
    "diluted_eps": ("Diluted EPS",),
    "operating_cash_flow": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
    "capital_expenditure": ("Capital Expenditure",),
    "free_cash_flow": ("Free Cash Flow",),
    "dividends_paid": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    "share_repurchases": ("Repurchase Of Capital Stock", "Common Stock Payments"),
    "stock_based_compensation": ("Stock Based Compensation",),
    "depreciation": ("Depreciation And Amortization", "Reconciled Depreciation", "Depreciation Amortization Depletion"),
    "diluted_shares": ("Diluted Average Shares",),
    "shares_outstanding": ("Ordinary Shares Number", "Share Issued"),
    "total_assets": ("Total Assets",),
    "current_assets": ("Current Assets",),
    "cash_and_investments": ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"),
    "receivables": ("Accounts Receivable", "Receivables"),
    "inventory": ("Inventory",),
    "total_liabilities": ("Total Liabilities Net Minority Interest",),
    "current_liabilities": ("Current Liabilities",),
    "total_debt": ("Total Debt",),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "shareholders_equity": ("Stockholders Equity", "Common Stock Equity"),
    "retained_earnings": ("Retained Earnings",),
}
_OUTFLOW_FIELDS = {"interest_expense", "capital_expenditure", "dividends_paid", "share_repurchases"}


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

    def get_financial_statements(self, ticker: str) -> list[FinancialStatementData]:
        symbol = ticker.upper().strip()
        stock = yf.Ticker(symbol)
        statements: list[FinancialStatementData] = []
        for period, frames in (
            ("annual", ("income_stmt", "balance_sheet", "cashflow")),
            ("quarterly", ("quarterly_income_stmt", "quarterly_balance_sheet", "quarterly_cashflow")),
        ):
            rows: dict[date, dict[str, float]] = {}
            for attribute in frames:
                try:
                    frame = getattr(stock, attribute)
                except Exception:
                    continue
                for fiscal_date, values in _frame_columns(frame).items():
                    rows.setdefault(fiscal_date, {}).update(values)
            for fiscal_date in sorted(rows):
                statement = _statement_from_rows(fiscal_date, period, rows[fiscal_date])
                if statement is not None:
                    statements.append(statement)
        return statements


def _frame_columns(frame: Any) -> dict[date, dict[str, float]]:
    """yfinance statement frame (row labels x period-end columns) -> {period end: {label: value}}."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    columns: dict[date, dict[str, float]] = {}
    for column in frame.columns:
        fiscal_date = column.date() if hasattr(column, "date") else column
        values = {}
        for label, value in frame[column].items():
            parsed = _number(value)
            if parsed is not None:
                values[str(label)] = parsed
        columns[fiscal_date] = values
    return columns


def _statement_from_rows(fiscal_date: date, period: str, rows: dict[str, float]) -> Optional[FinancialStatementData]:
    values: dict[str, Optional[float]] = {}
    for field in STATEMENT_FIELDS:
        value = next((rows[label] for label in _STATEMENT_ROWS[field] if label in rows), None)
        values[field] = abs(value) if value is not None and field in _OUTFLOW_FIELDS else value
    # yfinance pads older columns with empty rows; skip periods with neither revenue nor assets.
    if values["revenue"] is None and values["total_assets"] is None:
        return None
    return FinancialStatementData(fiscal_date=fiscal_date, period=period, **values)


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
