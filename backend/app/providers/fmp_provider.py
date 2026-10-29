from __future__ import annotations

from datetime import date
import json
import logging
from math import isfinite
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.providers.base import (
    STATEMENT_FIELDS,
    CompanyProfile,
    FinancialStatementData,
    FundamentalsData,
    MarketDataProvider,
    PriceBarData,
)

logger = logging.getLogger(__name__)

# Statement field -> FMP keys (stable and legacy v3 spellings), first match wins.
_STATEMENT_KEYS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue",),
    "cost_of_revenue": ("costOfRevenue",),
    "gross_profit": ("grossProfit",),
    "operating_income": ("operatingIncome", "ebit"),
    "ebitda": ("ebitda",),
    "interest_expense": ("interestExpense",),
    "pretax_income": ("incomeBeforeTax",),
    "income_tax": ("incomeTaxExpense",),
    "net_income": ("netIncome",),
    "diluted_eps": ("epsDiluted", "epsdiluted"),
    "operating_cash_flow": ("operatingCashFlow", "netCashProvidedByOperatingActivities"),
    "capital_expenditure": ("capitalExpenditure", "investmentsInPropertyPlantAndEquipment"),
    "free_cash_flow": ("freeCashFlow",),
    "dividends_paid": ("commonDividendsPaid", "netDividendsPaid", "dividendsPaid"),
    "share_repurchases": ("commonStockRepurchased",),
    "stock_based_compensation": ("stockBasedCompensation",),
    "depreciation": ("depreciationAndAmortization",),
    "diluted_shares": ("weightedAverageShsOutDil",),
    "shares_outstanding": ("weightedAverageShsOut",),
    "total_assets": ("totalAssets",),
    "current_assets": ("totalCurrentAssets",),
    "cash_and_investments": ("cashAndShortTermInvestments", "cashAndCashEquivalents"),
    "receivables": ("netReceivables", "accountsReceivables"),
    "inventory": ("inventory",),
    "total_liabilities": ("totalLiabilities",),
    "current_liabilities": ("totalCurrentLiabilities",),
    "total_debt": ("totalDebt",),
    "long_term_debt": ("longTermDebt",),
    "shareholders_equity": ("totalStockholdersEquity", "totalEquity"),
    "retained_earnings": ("retainedEarnings",),
}
_OUTFLOW_FIELDS = {"interest_expense", "capital_expenditure", "dividends_paid", "share_repurchases"}
_STATEMENT_ENDPOINTS = ("income-statement", "balance-sheet-statement", "cash-flow-statement")


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _first_number(payload: dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _number(payload.get(key))
        if value is not None:
            return value
    return None


def _as_decimal(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return value / 100 if abs(value) > 1 else value


class FMPProvider(MarketDataProvider):
    base_url = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FMP API key is required")
        self.api_key = api_key

    def get_company_profile(self, ticker: str) -> CompanyProfile:
        symbol = ticker.upper().strip()
        profile = self._first("profile", symbol)
        return CompanyProfile(
            ticker=symbol,
            name=str(profile.get("companyName") or profile.get("companyName") or profile.get("name") or symbol),
            sector=str(profile.get("sector") or ""),
            exchange=str(profile.get("exchangeShortName") or profile.get("exchange") or ""),
        )

    def get_price_history(self, ticker: str, period: str = "5y") -> list[PriceBarData]:
        raise NotImplementedError("FMPProvider is used for fundamentals only")

    def get_fundamentals(self, ticker: str) -> FundamentalsData:
        symbol = ticker.upper().strip()
        profile = self._first("profile", symbol)
        ratios = self._first("ratios-ttm", symbol)
        key_metrics = self._first("key-metrics-ttm", symbol)
        growth = self._first("financial-growth", symbol)
        cash_flow = self._first("cash-flow-statement", symbol)
        balance_sheet = self._first("balance-sheet-statement", symbol)

        total_debt = _first_number(balance_sheet, "totalDebt", "shortTermDebt", "longTermDebt")
        equity = _first_number(balance_sheet, "totalStockholdersEquity", "totalEquity", "totalShareholdersEquity")
        calculated_debt_to_equity = total_debt / equity if total_debt is not None and equity not in (None, 0) else None

        return FundamentalsData(
            date=date.today(),
            pe=_first_number(
                ratios,
                "priceToEarningsRatioTTM",
                "priceEarningsRatioTTM",
                "peRatioTTM",
                "priceEarningsRatio",
            )
            or _first_number(key_metrics, "peRatioTTM", "peRatio"),
            forward_pe=_first_number(profile, "forwardPE", "forwardPe"),
            eps_growth=_as_decimal(_first_number(growth, "growthEPS", "epsgrowth", "growthNetIncomePerShare")),
            revenue_growth=_as_decimal(_first_number(growth, "growthRevenue", "revenueGrowth")),
            debt_to_equity=_first_number(ratios, "debtEquityRatioTTM", "debtToEquityRatioTTM", "debtEquityRatio")
            or calculated_debt_to_equity,
            free_cash_flow=_first_number(cash_flow, "freeCashFlow")
            or _first_number(key_metrics, "freeCashFlowTTM", "freeCashFlowPerShareTTM"),
            dividend_yield=_as_decimal(
                _first_number(key_metrics, "dividendYieldTTM", "dividendYield")
                or _first_number(ratios, "dividendYieldTTM", "dividendYield")
            ),
        )

    def get_financial_statements(self, ticker: str) -> list[FinancialStatementData]:
        symbol = ticker.upper().strip()
        statements: list[FinancialStatementData] = []
        for period, fmp_period, limit in (("annual", "annual", "5"), ("quarterly", "quarter", "8")):
            rows: dict[date, dict[str, Any]] = {}
            for endpoint in _STATEMENT_ENDPOINTS:
                try:
                    payload = self._get(endpoint, {"symbol": symbol, "period": fmp_period, "limit": limit})
                except Exception as exc:
                    # Quarterly statements need a paid FMP plan; annual alone is still useful.
                    logger.info("FMP %s %s (%s) unavailable: %s", endpoint, symbol, period, exc)
                    continue
                for item in payload if isinstance(payload, list) else []:
                    if not isinstance(item, dict):
                        continue
                    try:
                        fiscal_date = date.fromisoformat(str(item.get("date"))[:10])
                    except ValueError:
                        continue
                    rows.setdefault(fiscal_date, {}).update(item)
            for fiscal_date in sorted(rows):
                values: dict[str, Optional[float]] = {}
                for field in STATEMENT_FIELDS:
                    value = _first_number(rows[fiscal_date], *_STATEMENT_KEYS[field])
                    values[field] = abs(value) if value is not None and field in _OUTFLOW_FIELDS else value
                if values["revenue"] is None and values["total_assets"] is None:
                    continue
                statements.append(FinancialStatementData(fiscal_date=fiscal_date, period=period, **values))
        return statements

    def _first(self, endpoint: str, symbol: str) -> dict[str, Any]:
        payload = self._get(endpoint, {"symbol": symbol})
        if isinstance(payload, list):
            first = payload[0] if payload else {}
            return first if isinstance(first, dict) else {}
        return payload if isinstance(payload, dict) else {}

    def _get(self, endpoint: str, params: dict[str, str]) -> Any:
        query = urlencode({**params, "apikey": self.api_key})
        request = Request(f"{self.base_url}/{endpoint}?{query}", headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
