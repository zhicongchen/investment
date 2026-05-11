from __future__ import annotations

from datetime import date
import json
from math import isfinite
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.providers.base import CompanyProfile, FundamentalsData, MarketDataProvider, PriceBarData


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
