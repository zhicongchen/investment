"""Financial-statement analysis and a buy / hold verdict.

Pure computation over duck-typed inputs (ORM rows or provider dataclasses), so it can be tested
without a database. The analysis is organised the way an equity analyst would write it up:

1. Ratio analysis from reported statements (TTM where four quarters are available, else the
   latest fiscal year) plus a five-year annual trend table.
2. Composite accounting scores: Piotroski F-score (fundamental momentum) and Altman Z-score
   (distress risk).
3. Intrinsic value: a two-stage free-cash-flow-to-firm DCF discounted at WACC (CAPM cost of
   equity with a regression beta against SPY), bear/base/bull scenarios, and a reverse DCF that
   solves for the growth the current price implies. Banks and insurers use a justified P/B model.
4. Five scored pillars (quality, financial strength, growth, valuation, momentum), red flags,
   and a verdict that depends on whether the position is already owned.

Every score is 0-100 and every number that feeds a score is returned, so the dashboard and the
AI memo can show their working.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Any, Optional, Sequence

from app.providers.base import STATEMENT_BALANCE_FIELDS, STATEMENT_FIELDS, STATEMENT_FLOW_FIELDS

TRADING_DAYS = 252
DEFAULT_TAX_RATE = 0.21
FINANCIAL_SECTORS = {"financial services", "financials", "financial", "banks", "insurance"}

PILLARS = (
    ("quality", "Profitability & Quality", 0.25),
    ("strength", "Financial Strength", 0.20),
    ("growth", "Growth", 0.20),
    ("valuation", "Valuation", 0.25),
    ("momentum", "Price Momentum", 0.10),
)

BUY_ACTIONS = {"BUY", "ADD"}


@dataclass(frozen=True)
class ValuationAssumptions:
    risk_free_rate: float = 0.0425
    equity_risk_premium: float = 0.05
    terminal_growth: float = 0.025
    required_margin_of_safety: float = 0.20
    min_discount_rate: float = 0.07
    max_discount_rate: float = 0.14
    max_stage_one_growth: float = 0.20
    stage_one_years: int = 5
    projection_years: int = 10


@dataclass
class _Period:
    fiscal_date: date
    values: dict[str, Optional[float]]

    def __getitem__(self, key: str) -> Optional[float]:
        return self.values.get(key)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------


def _div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _positive_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Ratio that is only meaningful over a positive base (equity, invested capital, earnings)."""
    if denominator is None or denominator <= 0:
        return None
    return _div(numerator, denominator)


def _growth(previous: Optional[float], current: Optional[float]) -> Optional[float]:
    if previous in (None, 0) or current is None:
        return None
    return (current - previous) / abs(previous)


def _cagr(start: Optional[float], end: Optional[float], years: float) -> Optional[float]:
    if start is None or end is None or start <= 0 or end <= 0 or years < 0.75:
        return None
    return (end / start) ** (1 / years) - 1


def _avg(*values: Optional[float]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _sub(left: Optional[float], right: Optional[float]) -> Optional[float]:
    return left - right if left is not None and right is not None else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _scale(value: Optional[float], points: Sequence[tuple[float, float]]) -> Optional[float]:
    """Piecewise-linear map of a metric onto 0-100 through (metric, score) anchor points."""
    if value is None:
        return None
    ordered = sorted(points)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _fmt_pct(value: Optional[float], digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _fmt_money(value: Optional[float]) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _fmt_x(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}x"


# ---------------------------------------------------------------------------
# Statement normalisation
# ---------------------------------------------------------------------------


def _period_from(row: Any) -> _Period:
    values = {field: getattr(row, field, None) for field in STATEMENT_FIELDS}
    return _Period(fiscal_date=row.fiscal_date, values=_fill_derived(values))


def _fill_derived(values: dict[str, Optional[float]]) -> dict[str, Optional[float]]:
    if values.get("gross_profit") is None:
        values["gross_profit"] = _sub(values.get("revenue"), values.get("cost_of_revenue"))
    if values.get("free_cash_flow") is None:
        values["free_cash_flow"] = _sub(values.get("operating_cash_flow"), values.get("capital_expenditure"))
    if values.get("ebitda") is None and values.get("operating_income") is not None and values.get("depreciation") is not None:
        values["ebitda"] = values["operating_income"] + values["depreciation"]
    return values


def _split_periods(statements: Sequence[Any]) -> tuple[list[_Period], list[_Period]]:
    annual = sorted((_period_from(row) for row in statements if row.period == "annual"), key=lambda p: p.fiscal_date)
    quarterly = sorted((_period_from(row) for row in statements if row.period == "quarterly"), key=lambda p: p.fiscal_date)
    return annual, quarterly


def _trailing_twelve_months(annual: list[_Period], quarterly: list[_Period]) -> tuple[Optional[_Period], str]:
    """Sum the last four consecutive quarters; fall back to the latest fiscal year."""
    latest_annual = annual[-1] if annual else None
    last_four = quarterly[-4:]
    consecutive = len(last_four) == 4 and (last_four[-1].fiscal_date - last_four[0].fiscal_date).days <= 300
    if consecutive and (latest_annual is None or last_four[-1].fiscal_date > latest_annual.fiscal_date):
        values: dict[str, Optional[float]] = {}
        for field in STATEMENT_FLOW_FIELDS:
            parts = [quarter[field] for quarter in last_four]
            values[field] = sum(parts) if all(part is not None for part in parts) else None
        for field in STATEMENT_BALANCE_FIELDS:
            values[field] = last_four[-1][field]
        return _Period(fiscal_date=last_four[-1].fiscal_date, values=_fill_derived(values)), "TTM"
    if latest_annual is not None:
        return latest_annual, "FY"
    return None, ""


def _tax_rate(period: _Period) -> float:
    rate = _positive_div(period["income_tax"], period["pretax_income"])
    return rate if rate is not None and 0 <= rate <= 0.5 else DEFAULT_TAX_RATE


def _nopat(period: _Period) -> Optional[float]:
    operating_income = period["operating_income"]
    return operating_income * (1 - _tax_rate(period)) if operating_income is not None else None


def _invested_capital(period: Optional[_Period]) -> Optional[float]:
    if period is None or period["shareholders_equity"] is None:
        return None
    capital = (period["total_debt"] or 0) + period["shareholders_equity"]
    return capital if capital > 0 else None


def _shares(period: Optional[_Period]) -> Optional[float]:
    if period is None:
        return None
    return period["shares_outstanding"] or period["diluted_shares"]


def _fcff(period: _Period) -> Optional[float]:
    """Free cash flow to the firm: reported FCF plus after-tax interest (FCF is after interest paid)."""
    fcf = period["free_cash_flow"]
    if fcf is None:
        return None
    return fcf + (period["interest_expense"] or 0) * (1 - _tax_rate(period))


# ---------------------------------------------------------------------------
# Prices: beta and momentum
# ---------------------------------------------------------------------------


def _adjusted(price: Any) -> Optional[float]:
    return price.adjusted_close if price.adjusted_close is not None else price.close


def _raw(price: Any) -> Optional[float]:
    return price.close if price.close is not None else price.adjusted_close


def _returns_by_date(prices: Sequence[Any]) -> dict[date, float]:
    returns: dict[date, float] = {}
    previous: Optional[float] = None
    for price in prices:
        close = _adjusted(price)
        if close is not None and previous:
            returns[price.date] = close / previous - 1
        if close is not None:
            previous = close
    return returns


def _beta(prices: Sequence[Any], benchmark: Sequence[Any]) -> Optional[float]:
    """Two-year daily regression beta vs the benchmark, Blume-adjusted toward 1."""
    stock = _returns_by_date(prices[-505:])
    market = _returns_by_date(benchmark[-505:])
    common = sorted(set(stock) & set(market))[-504:]
    if len(common) < 120:
        return None
    xs = [market[day] for day in common]
    ys = [stock[day] for day in common]
    x_mean, y_mean = mean(xs), mean(ys)
    variance = sum((x - x_mean) ** 2 for x in xs)
    if variance == 0:
        return None
    raw_beta = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / variance
    return 0.67 * raw_beta + 0.33


def _momentum(prices: Sequence[Any], benchmark: Sequence[Any]) -> dict[str, Optional[float]]:
    closes = [value for value in (_adjusted(price) for price in prices) if value is not None]
    bench = [value for value in (_adjusted(price) for price in benchmark) if value is not None]
    result: dict[str, Optional[float]] = {
        "price_vs_ma200": None,
        "ma50_vs_ma200": None,
        "return_12_1": None,
        "return_6m": None,
        "relative_strength_12m": None,
        "drawdown_52w": None,
        "volatility": None,
    }
    if not closes:
        return result
    latest = closes[-1]
    if len(closes) >= 200:
        ma200 = sum(closes[-200:]) / 200
        ma50 = sum(closes[-50:]) / 50
        result["price_vs_ma200"] = latest / ma200 - 1
        result["ma50_vs_ma200"] = ma50 / ma200 - 1
    if len(closes) > TRADING_DAYS:
        result["return_12_1"] = closes[-22] / closes[-TRADING_DAYS - 1] - 1
        stock_12m = latest / closes[-TRADING_DAYS - 1] - 1
        if len(bench) > TRADING_DAYS:
            result["relative_strength_12m"] = stock_12m - (bench[-1] / bench[-TRADING_DAYS - 1] - 1)
    if len(closes) > 126:
        result["return_6m"] = latest / closes[-127] - 1
    window = closes[-TRADING_DAYS:]
    result["drawdown_52w"] = latest / max(window) - 1
    daily = [b / a - 1 for a, b in zip(window, window[1:]) if a]
    if len(daily) >= 20:
        result["volatility"] = pstdev(daily) * math.sqrt(TRADING_DAYS)
    return result


# ---------------------------------------------------------------------------
# Accounting scores
# ---------------------------------------------------------------------------


def _gt(left: Optional[float], right: Optional[float]) -> Optional[bool]:
    return None if left is None or right is None else left > right


def piotroski_f_score(annual: Sequence[_Period]) -> Optional[dict[str, Any]]:
    """Nine binary tests of profitability, funding and efficiency trends (Piotroski, 2000)."""
    if len(annual) < 2:
        return None
    current, previous = annual[-1], annual[-2]
    before = annual[-3] if len(annual) >= 3 else None

    def roa(period: _Period, opening: Optional[_Period]) -> Optional[float]:
        return _positive_div(period["net_income"], (opening or period)["total_assets"])

    def leverage(period: _Period) -> Optional[float]:
        debt = period["long_term_debt"] if period["long_term_debt"] is not None else period["total_debt"]
        return _positive_div(debt, period["total_assets"])

    def current_ratio(period: _Period) -> Optional[float]:
        return _positive_div(period["current_assets"], period["current_liabilities"])

    def gross_margin(period: _Period) -> Optional[float]:
        return _positive_div(period["gross_profit"], period["revenue"])

    def turnover(period: _Period, opening: Optional[_Period]) -> Optional[float]:
        return _positive_div(period["revenue"], (opening or period)["total_assets"])

    roa_now, roa_before = roa(current, previous), roa(previous, before)
    lev_now, lev_before = leverage(current), leverage(previous)
    shares_now, shares_before = current["diluted_shares"] or _shares(current), previous["diluted_shares"] or _shares(previous)
    candidates = [
        ("Positive return on assets", None if roa_now is None else roa_now > 0),
        ("Positive operating cash flow", None if current["operating_cash_flow"] is None else current["operating_cash_flow"] > 0),
        ("Return on assets improved", _gt(roa_now, roa_before)),
        ("Operating cash flow exceeds net income", _gt(current["operating_cash_flow"], current["net_income"])),
        (
            "Long-term leverage fell",
            None if lev_now is None or lev_before is None else lev_now < lev_before or (lev_now == 0 and lev_before == 0),
        ),
        ("Current ratio improved", _gt(current_ratio(current), current_ratio(previous))),
        ("No share dilution", None if shares_now is None or shares_before is None else shares_now <= shares_before * 1.005),
        ("Gross margin improved", _gt(gross_margin(current), gross_margin(previous))),
        ("Asset turnover improved", _gt(turnover(current, previous), turnover(previous, before))),
    ]
    checks = [{"name": name, "passed": passed} for name, passed in candidates if passed is not None]
    if len(checks) < 6:
        return None
    passed = sum(1 for check in checks if check["passed"])
    return {
        "score": passed,
        "max": len(checks),
        "normalized": round(passed / len(checks) * 9, 1),
        "fiscal_date": current.fiscal_date,
        "checks": checks,
    }


def altman_z_score(period: _Period, market_cap: Optional[float]) -> Optional[float]:
    """Original Altman Z with market equity: >2.99 safe, 1.81-2.99 grey zone, <1.81 distress."""
    assets = period["total_assets"]
    liabilities = period["total_liabilities"]
    parts = [
        period["current_assets"],
        period["current_liabilities"],
        period["retained_earnings"],
        period["operating_income"],
        period["revenue"],
        market_cap,
    ]
    if not assets or not liabilities or any(part is None for part in parts):
        return None
    working_capital = period["current_assets"] - period["current_liabilities"]
    return (
        1.2 * working_capital / assets
        + 1.4 * period["retained_earnings"] / assets
        + 3.3 * period["operating_income"] / assets
        + 0.6 * market_cap / liabilities
        + 1.0 * period["revenue"] / assets
    )


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def dcf_enterprise_value(
    base_cash_flow: float,
    growth: float,
    discount_rate: float,
    terminal_growth: float,
    stage_one_years: int = 5,
    years: int = 10,
) -> tuple[float, float]:
    """Two-stage DCF: `growth` for stage one, then a linear fade to terminal growth.

    Returns (present value, share of value from the terminal value).
    """
    cash_flow = base_cash_flow
    present_value = 0.0
    for year in range(1, years + 1):
        if year <= stage_one_years:
            rate = growth
        else:
            rate = growth + (terminal_growth - growth) * (year - stage_one_years) / (years - stage_one_years)
        cash_flow *= 1 + rate
        present_value += cash_flow / (1 + discount_rate) ** year
    terminal = cash_flow * (1 + terminal_growth) / (discount_rate - terminal_growth)
    terminal_pv = terminal / (1 + discount_rate) ** years
    total = present_value + terminal_pv
    return total, terminal_pv / total if total else 0.0


def implied_growth(
    target_enterprise_value: float,
    base_cash_flow: float,
    discount_rate: float,
    terminal_growth: float,
    stage_one_years: int = 5,
    years: int = 10,
) -> Optional[float]:
    """Reverse DCF: the stage-one growth rate at which the DCF equals the market's enterprise value."""
    low, high = -0.30, 0.60

    def value(growth: float) -> float:
        return dcf_enterprise_value(base_cash_flow, growth, discount_rate, terminal_growth, stage_one_years, years)[0]

    if base_cash_flow <= 0 or value(low) > target_enterprise_value or value(high) < target_enterprise_value:
        return None
    for _ in range(80):
        middle = (low + high) / 2
        if value(middle) < target_enterprise_value:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _discount_rate(
    beta: Optional[float],
    market_cap: Optional[float],
    latest: _Period,
    assumptions: ValuationAssumptions,
) -> dict[str, Optional[float]]:
    used_beta = beta if beta is not None else 1.0
    cost_of_equity = assumptions.risk_free_rate + used_beta * assumptions.equity_risk_premium
    debt = latest["total_debt"] or 0
    implied_cost = _positive_div(latest["interest_expense"], debt)
    cost_of_debt = _clamp(
        implied_cost if implied_cost is not None else assumptions.risk_free_rate + 0.015,
        assumptions.risk_free_rate - 0.01,
        assumptions.risk_free_rate + 0.06,
    )
    after_tax_debt = cost_of_debt * (1 - _tax_rate(latest))
    if market_cap and market_cap > 0 and debt > 0:
        equity_weight = market_cap / (market_cap + debt)
        wacc = equity_weight * cost_of_equity + (1 - equity_weight) * after_tax_debt
    else:
        wacc = cost_of_equity
    return {
        "beta": beta,
        "cost_of_equity": cost_of_equity,
        "cost_of_debt": cost_of_debt,
        "wacc": _clamp(wacc, assumptions.min_discount_rate, assumptions.max_discount_rate),
    }


def _growth_assumption(
    revenue_cagr: Optional[float],
    fcf_cagr: Optional[float],
    forward_growth: Optional[float],
    reported_revenue_growth: Optional[float],
    assumptions: ValuationAssumptions,
) -> tuple[Optional[float], str]:
    inputs = [
        (revenue_cagr, 0.5, "revenue CAGR"),
        (fcf_cagr, 0.2, "FCF CAGR"),
        (forward_growth, 0.3, "forward EPS growth implied by forward P/E"),
    ]
    available = [(_clamp(value, -0.20, 0.40), weight, label) for value, weight, label in inputs if value is not None]
    if not available and reported_revenue_growth is not None:
        available = [(_clamp(reported_revenue_growth, -0.20, 0.40), 1.0, "latest reported revenue growth")]
    if not available:
        return None, ""
    total_weight = sum(weight for _, weight, _ in available)
    blended = sum(value * weight for value, weight, _ in available) / total_weight
    growth = _clamp(blended, -0.05, assumptions.max_stage_one_growth)
    source = " + ".join(label for _, _, label in available)
    return growth, f"Blend of {source}, capped at {_fmt_pct(assumptions.max_stage_one_growth, 0)}"


def _dcf_valuation(
    *,
    latest: _Period,
    annual: list[_Period],
    shares: Optional[float],
    market_cap: Optional[float],
    rates: dict[str, Optional[float]],
    growth: Optional[float],
    assumptions: ValuationAssumptions,
) -> dict[str, Any]:
    result: dict[str, Any] = {"method": "Two-stage DCF on free cash flow to firm", "applicable": False}
    candidates = [value for value in [_fcff(latest), _avg(*[_fcff(p) for p in annual[-3:]])] if value is not None]
    base = mean(candidates) if candidates else None
    result["base_cash_flow"] = base
    if base is None or base <= 0:
        result["note"] = "Free cash flow is negative or missing, so a cash-flow DCF is not meaningful."
        return result
    if growth is None or not shares or shares <= 0:
        result["note"] = "Growth history or share count missing; DCF skipped."
        return result

    wacc = rates["wacc"]
    terminal = assumptions.terminal_growth
    net_debt = (latest["total_debt"] or 0) - (latest["cash_and_investments"] or 0)

    def per_share(stage_growth: float, rate: float) -> tuple[float, float]:
        rate = max(rate, terminal + 0.03)
        enterprise, terminal_share = dcf_enterprise_value(
            base, stage_growth, rate, terminal, assumptions.stage_one_years, assumptions.projection_years
        )
        return max(enterprise - net_debt, 0) / shares, terminal_share

    bear_growth = growth * 0.5 if growth > 0 else growth - 0.03
    bull_growth = min(growth + max(0.03, growth * 0.3), assumptions.max_stage_one_growth + 0.05)
    base_value, terminal_share = per_share(growth, wacc)
    bear_value, _ = per_share(bear_growth, wacc + 0.01)
    bull_value, _ = per_share(bull_growth, wacc - 0.0075)

    market_growth = None
    if market_cap:
        market_growth = implied_growth(
            market_cap + net_debt, base, max(wacc, terminal + 0.03), terminal, assumptions.stage_one_years, assumptions.projection_years
        )
    result.update(
        {
            "applicable": True,
            "net_debt": net_debt,
            "stage_one_growth": growth,
            "terminal_growth": terminal,
            "bear_value": bear_value,
            "base_value": base_value,
            "bull_value": bull_value,
            "terminal_value_share": terminal_share,
            "implied_growth": market_growth,
        }
    )
    return result


def _justified_pb_valuation(
    *,
    latest: _Period,
    shares: Optional[float],
    roe: Optional[float],
    rates: dict[str, Optional[float]],
    assumptions: ValuationAssumptions,
) -> dict[str, Any]:
    """Banks and insurers: fair P/B = (ROE - g) / (cost of equity - g)."""
    result: dict[str, Any] = {"method": "Justified price-to-book (ROE vs cost of equity)", "applicable": False}
    book_per_share = _positive_div(latest["shareholders_equity"], shares)
    cost_of_equity = rates["cost_of_equity"]
    terminal = assumptions.terminal_growth
    if book_per_share is None or roe is None or cost_of_equity is None:
        result["note"] = "Book value or ROE missing; justified P/B skipped."
        return result

    def value(return_on_equity: float, rate: float) -> float:
        rate = max(rate, terminal + 0.02)
        return book_per_share * max(return_on_equity - terminal, 0) / (rate - terminal)

    result.update(
        {
            "applicable": True,
            "book_value_per_share": book_per_share,
            "terminal_growth": terminal,
            "bear_value": value(roe - 0.02, cost_of_equity + 0.01),
            "base_value": value(roe, cost_of_equity),
            "bull_value": value(roe + 0.02, cost_of_equity - 0.0075),
            "implied_growth": None,
        }
    )
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _metric(key: str, label: str, value: Optional[float], fmt: str, score: Optional[float], weight: float = 1.0, note: str = "") -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": _round(value),
        "format": fmt,
        "score": _round(score, 1),
        "weight": weight,
        "note": note,
    }


def _pillar_score(metrics: list[dict[str, Any]]) -> Optional[float]:
    scored = [(metric["score"], metric["weight"]) for metric in metrics if metric["score"] is not None]
    if len(scored) < 2:
        return None
    return round(sum(score * weight for score, weight in scored) / sum(weight for _, weight in scored), 1)


def _annual_table(annual: list[_Period]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous: Optional[_Period] = None
    for period in annual:
        equity = _avg(previous["shareholders_equity"] if previous else None, period["shareholders_equity"])
        capital = _avg(_invested_capital(previous), _invested_capital(period))
        rows.append(
            {
                "fiscal_date": period.fiscal_date,
                "revenue": period["revenue"],
                "revenue_growth": _round(_growth(previous["revenue"], period["revenue"]) if previous else None),
                "gross_margin": _round(_positive_div(period["gross_profit"], period["revenue"])),
                "operating_margin": _round(_positive_div(period["operating_income"], period["revenue"])),
                "net_margin": _round(_positive_div(period["net_income"], period["revenue"])),
                "free_cash_flow": period["free_cash_flow"],
                "fcf_margin": _round(_positive_div(period["free_cash_flow"], period["revenue"])),
                "roe": _round(_positive_div(period["net_income"], equity)),
                "roic": _round(_positive_div(_nopat(period), capital)),
                "debt_to_equity": _round(_positive_div(period["total_debt"], period["shareholders_equity"])),
                "current_ratio": _round(_positive_div(period["current_assets"], period["current_liabilities"])),
                "diluted_eps": period["diluted_eps"],
                "diluted_shares": period["diluted_shares"] or _shares(period),
            }
        )
        previous = period
    return rows[-5:]


def analyze_company(
    *,
    ticker: str,
    sector: str = "",
    statements: Sequence[Any],
    prices: Sequence[Any],
    benchmark_prices: Sequence[Any] = (),
    fundamentals: Any = None,
    holding: Any = None,
    portfolio_value: float = 0.0,
    assumptions: ValuationAssumptions = ValuationAssumptions(),
) -> dict[str, Any]:
    warnings: list[str] = []
    annual, quarterly = _split_periods(statements)
    latest, basis = _trailing_twelve_months(annual, quarterly)
    is_financial = sector.strip().lower() in FINANCIAL_SECTORS

    last_bar = prices[-1] if prices else None
    price = _raw(last_bar) if last_bar is not None else None
    as_of = last_bar.date if last_bar is not None else None
    momentum = _momentum(prices, benchmark_prices)
    beta = _beta(prices, benchmark_prices)

    if latest is None:
        warnings.append("No financial statements stored yet. Refresh this ticker with network access to download them.")
    elif basis == "FY":
        warnings.append("Quarterly statements unavailable; ratios use the latest fiscal year, not trailing twelve months.")
    if is_financial:
        warnings.append(
            "Financial-sector company: industrial ratios (current ratio, Altman Z, EBITDA leverage) are skipped "
            "and fair value uses a justified price-to-book model."
        )

    empty = latest or _Period(fiscal_date=as_of or date.today(), values={field: None for field in STATEMENT_FIELDS})
    shares = _shares(empty)
    market_cap = price * shares if price is not None and shares else None
    debt, cash = empty["total_debt"], empty["cash_and_investments"]
    net_debt = (debt or 0) - (cash or 0) if debt is not None or cash is not None else None
    enterprise_value = market_cap + net_debt if market_cap is not None and net_debt is not None else market_cap
    # Balance sheet one year before the analysis date, for average-capital return ratios.
    if basis == "TTM":
        opening = quarterly[-5] if len(quarterly) >= 5 else annual[-1] if annual else None
    else:
        opening = annual[-2] if len(annual) >= 2 else None

    # --- Profitability & quality ------------------------------------------------------------
    revenue = empty["revenue"]
    gross_margin = _positive_div(empty["gross_profit"], revenue)
    operating_margin = _positive_div(empty["operating_income"], revenue)
    net_margin = _positive_div(empty["net_income"], revenue)
    fcf_margin = _positive_div(empty["free_cash_flow"], revenue)
    roe = _positive_div(empty["net_income"], _avg(opening["shareholders_equity"] if opening else None, empty["shareholders_equity"]))
    roa = _positive_div(empty["net_income"], _avg(opening["total_assets"] if opening else None, empty["total_assets"]))
    roic = _positive_div(_nopat(empty), _avg(_invested_capital(opening), _invested_capital(empty)))
    fcf_conversion = _positive_div(empty["free_cash_flow"], empty["net_income"])
    average_assets = _avg(opening["total_assets"] if opening else None, empty["total_assets"])
    accruals = _positive_div(_sub(empty["net_income"], empty["operating_cash_flow"]), average_assets)
    sbc_to_revenue = _positive_div(empty["stock_based_compensation"], revenue)
    annual_operating_margins = [m for m in (_positive_div(p["operating_income"], p["revenue"]) for p in annual[-5:]) if m is not None]
    margin_volatility = pstdev(annual_operating_margins) if len(annual_operating_margins) >= 3 else None
    rates = _discount_rate(beta, market_cap, empty, assumptions)
    economic_spread = roic - rates["wacc"] if roic is not None and rates["wacc"] is not None else None
    piotroski = piotroski_f_score(annual)

    # --- Growth ------------------------------------------------------------------------------
    start = annual[-4] if len(annual) >= 4 else (annual[0] if len(annual) >= 2 else None)
    end = annual[-1] if annual else None
    years = (end.fiscal_date - start.fiscal_date).days / 365.25 if start and end else 0
    revenue_cagr = _cagr(start["revenue"], end["revenue"], years) if start and end else None
    eps_cagr = _cagr(start["diluted_eps"], end["diluted_eps"], years) if start and end else None
    fcf_cagr = _cagr(start["free_cash_flow"], end["free_cash_flow"], years) if start and end else None
    share_change = _cagr(start["diluted_shares"] or _shares(start), end["diluted_shares"] or _shares(end), years) if start and end else None
    margin_trend = (
        _sub(_positive_div(end["operating_income"], end["revenue"]), _positive_div(start["operating_income"], start["revenue"]))
        if start and end
        else None
    )
    yearly_growth = [_growth(a["revenue"], b["revenue"]) for a, b in zip(annual, annual[1:])]
    revenue_declining = len(yearly_growth) >= 2 and all(g is not None and g < 0 for g in yearly_growth[-2:])
    latest_revenue_growth = yearly_growth[-1] if yearly_growth else None

    # --- Financial strength -------------------------------------------------------------------
    current_ratio = _positive_div(empty["current_assets"], empty["current_liabilities"])
    quick_ratio = _positive_div(_sub(empty["current_assets"], empty["inventory"] or 0), empty["current_liabilities"])
    debt_to_equity = _positive_div(empty["total_debt"], empty["shareholders_equity"])
    net_debt_to_ebitda = _positive_div(net_debt, empty["ebitda"])
    interest_coverage = _positive_div(empty["operating_income"], empty["interest_expense"]) if (empty["interest_expense"] or 0) > 0 else None
    negligible_interest = (empty["interest_expense"] or 0) <= 0 and (_positive_div(empty["total_debt"] or 0, empty["total_assets"]) or 0) < 0.1
    equity_to_assets = _positive_div(empty["shareholders_equity"], empty["total_assets"])
    altman = None if is_financial or latest is None else altman_z_score(empty, market_cap)

    # --- Valuation multiples ------------------------------------------------------------------
    pe = _positive_div(market_cap, empty["net_income"]) if empty["net_income"] and empty["net_income"] > 0 else None
    forward_pe = getattr(fundamentals, "forward_pe", None) if fundamentals is not None else None
    forward_pe = forward_pe if forward_pe and forward_pe > 0 else None
    # Compare forward P/E with the same provider's trailing P/E so both use one EPS definition.
    provider_pe = getattr(fundamentals, "pe", None) if fundamentals is not None else None
    trailing_pe = provider_pe if provider_pe and provider_pe > 0 else pe
    forward_growth = trailing_pe / forward_pe - 1 if trailing_pe and forward_pe else None
    if forward_growth is not None and abs(forward_growth) > 0.6:
        forward_growth = None  # inconsistent provider data, not a forecast
    peg_growth = eps_cagr if eps_cagr is not None and eps_cagr > 0 else (forward_growth if forward_growth and forward_growth > 0 else None)
    peg = pe / (peg_growth * 100) if pe and peg_growth else None
    ev_to_ebitda = _positive_div(enterprise_value, empty["ebitda"])
    ev_to_sales = _positive_div(enterprise_value, revenue)
    price_to_book = _positive_div(market_cap, empty["shareholders_equity"])
    fcf_yield = _positive_div(empty["free_cash_flow"], market_cap)
    earnings_yield = _positive_div(empty["operating_income"], enterprise_value)
    dividend_yield = _positive_div(empty["dividends_paid"], market_cap)
    if dividend_yield is None and fundamentals is not None:
        dividend_yield = getattr(fundamentals, "dividend_yield", None)
    buyback_yield = _positive_div(empty["share_repurchases"], market_cap)
    payout_ratio = _positive_div(empty["dividends_paid"], empty["net_income"])

    growth_assumption, growth_source = _growth_assumption(
        revenue_cagr,
        fcf_cagr,
        forward_growth,
        getattr(fundamentals, "revenue_growth", None) if fundamentals is not None else None,
        assumptions,
    )
    if latest is None:
        valuation = {"method": "", "applicable": False, "note": "No statements to value."}
    elif is_financial:
        valuation = _justified_pb_valuation(latest=empty, shares=shares, roe=roe, rates=rates, assumptions=assumptions)
    else:
        valuation = _dcf_valuation(
            latest=empty,
            annual=annual,
            shares=shares,
            market_cap=market_cap,
            rates=rates,
            growth=growth_assumption,
            assumptions=assumptions,
        )
    base_value = valuation.get("base_value")
    margin_of_safety = (base_value - price) / base_value if base_value and price is not None else None
    upside = base_value / price - 1 if base_value and price else None
    entry_price = base_value * (1 - assumptions.required_margin_of_safety) if base_value else None
    trim_price = max(valuation.get("bull_value") or 0, base_value * 1.25) if base_value else None

    # --- Pillars ------------------------------------------------------------------------------
    quality_metrics = [
        _metric("roic", "Return on invested capital", roic, "pct",
                None if is_financial else _scale(roic, [(0, 5), (0.08, 50), (0.15, 80), (0.25, 100)]), 1.5,
                f"vs {_fmt_pct(rates['wacc'])} cost of capital" if rates["wacc"] is not None and roic is not None else ""),
        _metric("roe", "Return on equity", roe, "pct", _scale(roe, [(0, 5), (0.08, 40), (0.15, 70), (0.25, 95)]) if is_financial else None),
        _metric("gross_margin", "Gross margin", gross_margin, "pct",
                None if is_financial else _scale(gross_margin, [(0.15, 20), (0.35, 50), (0.55, 80), (0.75, 95)])),
        _metric("operating_margin", "Operating margin", operating_margin, "pct", _scale(operating_margin, [(0, 10), (0.10, 50), (0.20, 75), (0.30, 95)])),
        _metric("net_margin", "Net margin", net_margin, "pct", None),
        _metric("fcf_margin", "Free cash flow margin", fcf_margin, "pct",
                None if is_financial else _scale(fcf_margin, [(0, 10), (0.05, 40), (0.15, 75), (0.25, 95)])),
        _metric("fcf_conversion", "FCF / net income", fcf_conversion, "x",
                None if is_financial else _scale(fcf_conversion, [(0.4, 15), (0.8, 60), (1.0, 80), (1.2, 90)])),
        _metric("accruals", "Accruals ratio", accruals, "pct", _scale(accruals, [(-0.05, 90), (0, 70), (0.05, 40), (0.10, 15)]), 1.0,
                "(net income - operating cash flow) / assets; lower is cleaner earnings"),
        _metric("margin_volatility", "Operating margin volatility", margin_volatility, "pct",
                _scale(margin_volatility, [(0.02, 90), (0.05, 60), (0.10, 25)]), 0.75),
        _metric("piotroski", "Piotroski F-score", piotroski["normalized"] if piotroski else None, "score",
                _scale(piotroski["normalized"] if piotroski else None, [(2, 10), (4, 40), (6, 70), (8, 92), (9, 100)]), 1.25, "out of 9"),
        _metric("share_change", "Share count change / yr", share_change, "pct",
                _scale(share_change, [(-0.04, 95), (-0.02, 80), (0, 60), (0.02, 35), (0.05, 10)]), 0.75),
        _metric("sbc_to_revenue", "Stock comp / revenue", sbc_to_revenue, "pct", _scale(sbc_to_revenue, [(0.01, 85), (0.05, 55), (0.12, 20)]), 0.5),
    ]

    if is_financial:
        strength_metrics = [
            _metric("equity_to_assets", "Equity / assets", equity_to_assets, "pct", _scale(equity_to_assets, [(0.05, 20), (0.08, 50), (0.11, 75), (0.15, 90)]), 1.5),
            _metric("debt_to_equity", "Debt / equity", debt_to_equity, "x", _scale(debt_to_equity, [(0.5, 85), (1.5, 60), (3, 30), (5, 10)])),
            _metric("payout_ratio", "Dividend payout ratio", payout_ratio, "pct", _scale(payout_ratio, [(0.3, 85), (0.6, 65), (0.9, 35), (1.2, 10)]), 0.5),
        ]
    else:
        strength_metrics = [
            _metric("altman_z", "Altman Z-score", altman, "score", _scale(altman, [(1.0, 10), (1.81, 35), (2.99, 70), (5, 95)]), 1.25,
                    ">2.99 safe, 1.81-2.99 grey zone, <1.81 distress"),
            _metric("interest_coverage", "Interest coverage (EBIT / interest)", interest_coverage, "x",
                    95 if negligible_interest else _scale(interest_coverage, [(1.5, 10), (3, 40), (8, 75), (20, 95)]), 1.0,
                    "no meaningful interest expense" if negligible_interest else ""),
            _metric("net_debt_to_ebitda", "Net debt / EBITDA", net_debt_to_ebitda, "x",
                    _scale(net_debt_to_ebitda, [(-1, 100), (0, 90), (1, 75), (2, 60), (3, 40), (4.5, 10)]), 1.25,
                    "negative means net cash"),
            _metric("current_ratio", "Current ratio", current_ratio, "x", _scale(current_ratio, [(0.7, 20), (1.0, 45), (1.5, 75), (2.5, 90)])),
            _metric("quick_ratio", "Quick ratio", quick_ratio, "x", None),
            _metric("debt_to_equity", "Debt / equity", debt_to_equity, "x", _scale(debt_to_equity, [(0, 95), (0.5, 80), (1, 60), (2, 35), (3, 15)]), 0.75,
                    "" if debt_to_equity is not None or empty["shareholders_equity"] is None or empty["shareholders_equity"] > 0 else "negative equity (often from buybacks)"),
            _metric("payout_ratio", "Dividend payout ratio", payout_ratio, "pct", _scale(payout_ratio, [(0.4, 85), (0.7, 60), (1.0, 30), (1.3, 10)]), 0.5),
        ]

    growth_metrics = [
        _metric("revenue_cagr", "Revenue CAGR (3y)", revenue_cagr, "pct", _scale(revenue_cagr, [(-0.05, 10), (0, 30), (0.05, 55), (0.10, 75), (0.20, 95)]), 1.5),
        _metric("eps_cagr", "Diluted EPS CAGR (3y)", eps_cagr, "pct", _scale(eps_cagr, [(-0.10, 10), (0, 30), (0.08, 60), (0.15, 80), (0.25, 95)])),
        _metric("fcf_cagr", "Free cash flow CAGR (3y)", fcf_cagr, "pct", _scale(fcf_cagr, [(-0.10, 10), (0, 30), (0.08, 60), (0.15, 80), (0.25, 95)])),
        _metric("latest_revenue_growth", "Revenue growth (latest FY)", latest_revenue_growth, "pct",
                _scale(latest_revenue_growth, [(-0.05, 10), (0, 30), (0.05, 55), (0.10, 75), (0.20, 95)]), 0.75),
        _metric("margin_trend", "Operating margin change (3y)", margin_trend, "pp", _scale(margin_trend, [(-0.05, 15), (0, 50), (0.03, 75), (0.06, 90)]), 0.75),
        _metric("forward_growth", "Forward EPS growth (implied)", forward_growth, "pct", _scale(forward_growth, [(-0.05, 15), (0, 35), (0.10, 65), (0.20, 85), (0.30, 95)]), 0.75,
                "trailing P/E / forward P/E - 1"),
    ]

    valuation_metrics = [
        _metric("margin_of_safety", "Margin of safety", margin_of_safety, "pct",
                _scale(margin_of_safety, [(-0.5, 5), (-0.2, 30), (0, 50), (0.2, 75), (0.4, 95)]), 2.0,
                f"base-case fair value {_fmt_money(base_value)}" if base_value else ""),
        _metric("fcf_yield", "Free cash flow yield", fcf_yield, "pct", None if is_financial else _scale(fcf_yield, [(0.01, 15), (0.03, 45), (0.05, 70), (0.08, 90)])),
        _metric("earnings_yield", "EBIT / enterprise value", earnings_yield, "pct",
                None if is_financial else _scale(_sub(earnings_yield, assumptions.risk_free_rate), [(-0.02, 15), (0, 40), (0.03, 70), (0.06, 90)]), 0.75,
                f"vs {_fmt_pct(assumptions.risk_free_rate, 2)} risk-free rate"),
        _metric("pe", "P/E (TTM)", pe, "x", _scale(pe, [(10, 90), (15, 75), (22, 55), (30, 35), (45, 10)]), 0.75),
        _metric("forward_pe", "Forward P/E", forward_pe, "x", _scale(forward_pe, [(10, 90), (15, 75), (22, 50), (30, 30), (40, 15)]), 0.75),
        _metric("peg", "PEG ratio", peg, "x", _scale(peg, [(0.7, 90), (1.0, 75), (1.5, 55), (2.0, 35), (3.0, 15)]), 0.75),
        _metric("ev_to_ebitda", "EV / EBITDA", ev_to_ebitda, "x", None if is_financial else _scale(ev_to_ebitda, [(6, 90), (10, 70), (14, 50), (20, 30), (30, 10)]), 0.75),
        _metric("ev_to_sales", "EV / sales", ev_to_sales, "x", None),
        _metric("price_to_book", "Price / book", price_to_book, "x", _scale(price_to_book, [(0.8, 90), (1.2, 70), (2.0, 45), (3.0, 20)]) if is_financial else None),
        _metric("dividend_yield", "Dividend yield", dividend_yield, "pct", None),
        _metric("buyback_yield", "Buyback yield", buyback_yield, "pct", None),
    ]

    momentum_metrics = [
        _metric("price_vs_ma200", "Price vs 200-day average", momentum["price_vs_ma200"], "pct",
                _scale(momentum["price_vs_ma200"], [(-0.20, 10), (-0.05, 35), (0, 50), (0.05, 65), (0.15, 80), (0.40, 60)])),
        _metric("return_12_1", "12-1 month return", momentum["return_12_1"], "pct", _scale(momentum["return_12_1"], [(-0.30, 10), (0, 40), (0.15, 65), (0.40, 90)])),
        _metric("relative_strength_12m", "12-month return vs SPY", momentum["relative_strength_12m"], "pp",
                _scale(momentum["relative_strength_12m"], [(-0.20, 15), (0, 50), (0.20, 85)])),
        _metric("drawdown_52w", "Below 52-week high", momentum["drawdown_52w"], "pct", _scale(momentum["drawdown_52w"], [(-0.40, 20), (-0.20, 45), (-0.05, 70), (0, 75)]), 0.75),
        _metric("volatility", "Annualized volatility", momentum["volatility"], "pct", None),
        _metric("beta", "Beta vs SPY", beta, "score", None),
    ]

    metric_groups = {
        "quality": quality_metrics,
        "strength": strength_metrics,
        "growth": growth_metrics,
        "valuation": valuation_metrics,
        "momentum": momentum_metrics,
    }
    pillars = [
        {"key": key, "label": label, "weight": weight, "score": _pillar_score(metric_groups[key]), "metrics": metric_groups[key]}
        for key, label, weight in PILLARS
    ]
    scores = {pillar["key"]: pillar["score"] for pillar in pillars}
    available_weight = sum(pillar["weight"] for pillar in pillars if pillar["score"] is not None)
    composite = (
        round(sum(pillar["score"] * pillar["weight"] for pillar in pillars if pillar["score"] is not None) / available_weight, 1)
        if available_weight
        else None
    )
    coverage = round(available_weight / sum(weight for _, _, weight in PILLARS), 2)

    flags = _red_flags(
        latest=latest,
        is_financial=is_financial,
        altman=altman,
        interest_coverage=interest_coverage,
        net_debt=net_debt,
        net_debt_to_ebitda=net_debt_to_ebitda,
        accruals=accruals,
        piotroski=piotroski,
        share_change=share_change,
        revenue_declining=revenue_declining,
        payout_ratio=payout_ratio,
        equity=empty["shareholders_equity"],
        momentum=momentum,
        implied=valuation.get("implied_growth"),
        revenue_cagr=revenue_cagr,
        margin_of_safety=margin_of_safety,
    )

    owned = bool(holding is not None and (getattr(holding, "shares", 0) or 0) > 0)
    position = _position_context(holding, price, portfolio_value) if owned else None
    recommendation = _recommendation(
        ticker=ticker,
        scores=scores,
        composite=composite,
        coverage=coverage,
        flags=flags,
        owned=owned,
        position=position,
        price=price,
        valuation=valuation,
        margin_of_safety=margin_of_safety,
        entry_price=entry_price,
        trim_price=trim_price,
        is_financial=is_financial,
        metric_groups=metric_groups,
        assumptions=assumptions,
        roic=roic,
        wacc=rates["wacc"],
        revenue_cagr=revenue_cagr,
    )

    return {
        "ticker": ticker,
        "sector": sector,
        "as_of": as_of,
        "statement_date": latest.fiscal_date if latest else None,
        "statement_basis": basis,
        "is_financial_sector": is_financial,
        "data_coverage": coverage,
        "price": _round(price),
        "market_cap": _round(market_cap, 0),
        "enterprise_value": _round(enterprise_value, 0),
        "pillars": pillars,
        "composite_score": composite,
        "annual": _annual_table(annual),
        "piotroski": piotroski,
        "altman_z": _round(altman, 2),
        "economic_spread": _round(economic_spread),
        "valuation": {
            "method": valuation.get("method", ""),
            "applicable": valuation.get("applicable", False),
            "note": valuation.get("note", ""),
            "price": _round(price),
            "bear_value": _round(valuation.get("bear_value"), 2),
            "base_value": _round(base_value, 2),
            "bull_value": _round(valuation.get("bull_value"), 2),
            "margin_of_safety": _round(margin_of_safety),
            "upside": _round(upside),
            "entry_price": _round(entry_price, 2),
            "trim_price": _round(trim_price, 2),
            "discount_rate": _round(rates["wacc"]),
            "cost_of_equity": _round(rates["cost_of_equity"]),
            "beta": _round(beta, 2),
            "stage_one_growth": _round(valuation.get("stage_one_growth")),
            "growth_source": growth_source if valuation.get("stage_one_growth") is not None else "",
            "terminal_growth": _round(valuation.get("terminal_growth")),
            "terminal_value_share": _round(valuation.get("terminal_value_share")),
            "implied_growth": _round(valuation.get("implied_growth")),
            "base_cash_flow": _round(valuation.get("base_cash_flow"), 0),
            "required_margin_of_safety": assumptions.required_margin_of_safety,
        },
        "flags": flags,
        "position": position,
        "recommendation": recommendation,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Flags and verdict
# ---------------------------------------------------------------------------


def _red_flags(
    *,
    latest: Optional[_Period],
    is_financial: bool,
    altman: Optional[float],
    interest_coverage: Optional[float],
    net_debt: Optional[float],
    net_debt_to_ebitda: Optional[float],
    accruals: Optional[float],
    piotroski: Optional[dict[str, Any]],
    share_change: Optional[float],
    revenue_declining: bool,
    payout_ratio: Optional[float],
    equity: Optional[float],
    momentum: dict[str, Optional[float]],
    implied: Optional[float],
    revenue_cagr: Optional[float],
    margin_of_safety: Optional[float],
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []

    def add(severity: str, message: str) -> None:
        flags.append({"severity": severity, "message": message})

    if latest is not None:
        operating_cash_flow = latest["operating_cash_flow"]
        free_cash_flow = latest["free_cash_flow"]
        if operating_cash_flow is not None and operating_cash_flow < 0:
            add("high", "Operating cash flow is negative: the business is consuming cash.")
        elif free_cash_flow is not None and free_cash_flow < 0:
            add("medium", "Free cash flow is negative after capital spending.")
        if latest["net_income"] is not None and latest["net_income"] < 0:
            add("medium", "The company is loss-making on a trailing basis.")
    if not is_financial:
        if altman is not None and altman < 1.81:
            add("high", f"Altman Z-score {altman:.2f} is in the distress zone (< 1.81).")
        if interest_coverage is not None and interest_coverage < 0:
            add("high", "Operating income is negative, so interest payments are not covered by operations.")
        elif interest_coverage is not None and interest_coverage < 2:
            add("high" if interest_coverage < 1.5 else "medium", f"Interest coverage is thin at {interest_coverage:.1f}x.")
        if net_debt is not None and net_debt > 0 and latest is not None and latest["ebitda"] is not None and latest["ebitda"] <= 0:
            add("high", "EBITDA is negative while the company carries net debt.")
        elif net_debt_to_ebitda is not None and net_debt_to_ebitda > 4:
            add("high", f"Net debt is {net_debt_to_ebitda:.1f}x EBITDA, a heavy debt load.")
        elif net_debt_to_ebitda is not None and net_debt_to_ebitda > 3:
            add("medium", f"Net debt is {net_debt_to_ebitda:.1f}x EBITDA.")
    if accruals is not None and accruals > 0.10:
        add("medium", f"Accruals ratio {_fmt_pct(accruals)}: earnings are running well ahead of cash flow.")
    if piotroski and piotroski["normalized"] <= 3:
        add("medium", f"Piotroski F-score {piotroski['score']}/{piotroski['max']}: fundamentals deteriorated last year.")
    if share_change is not None and share_change > 0.03:
        add("medium", f"Share count grew {_fmt_pct(share_change)} a year: shareholders are being diluted.")
    if revenue_declining:
        add("medium", "Revenue declined in each of the last two fiscal years.")
    if payout_ratio is not None and payout_ratio > 1:
        add("medium", f"Dividends are {_fmt_pct(payout_ratio, 0)} of earnings and not covered by profits.")
    if equity is not None and equity <= 0:
        add("low", "Shareholders' equity is negative (common after large buybacks); book-value ratios are not meaningful.")
    if implied is not None and revenue_cagr is not None and implied > max(revenue_cagr * 1.5, revenue_cagr + 0.05):
        add("medium", f"The price implies {_fmt_pct(implied)} annual cash-flow growth for 5 years vs {_fmt_pct(revenue_cagr)} historical revenue growth.")
    if margin_of_safety is not None and margin_of_safety < -0.5:
        add("medium", "Price is more than 50% above the base-case fair value estimate.")
    if momentum["price_vs_ma200"] is not None and momentum["ma50_vs_ma200"] is not None and momentum["price_vs_ma200"] < 0 and momentum["ma50_vs_ma200"] < 0:
        add("low", "Price is in a downtrend (below the 200-day average, 50-day below 200-day).")
    return flags


def _position_context(holding: Any, price: Optional[float], portfolio_value: float) -> dict[str, Optional[float]]:
    shares = holding.shares or 0
    average_cost = holding.average_cost or 0
    market_value = shares * price if price is not None else None
    allocation = market_value / portfolio_value if market_value is not None and portfolio_value > 0 else None
    target = holding.target_allocation or None
    return {
        "shares": shares,
        "average_cost": average_cost,
        "market_value": _round(market_value, 2),
        "unrealized_return": _round(price / average_cost - 1 if price is not None and average_cost > 0 else None),
        "allocation": _round(allocation),
        "target_allocation": target,
        "overweight": bool(target and allocation is not None and allocation > target * 1.35),
        "underweight": bool(target and allocation is not None and allocation < target * 0.75),
    }


def _bucket(score: Optional[float], phrases: tuple[str, str, str, str]) -> Optional[str]:
    if score is None:
        return None
    if score >= 75:
        return phrases[0]
    if score >= 55:
        return phrases[1]
    if score >= 35:
        return phrases[2]
    return phrases[3]


def _format_metric(metric: dict[str, Any]) -> str:
    value = metric["value"]
    if value is None:
        return metric["note"] or "n/a"
    if metric["format"] == "pct":
        return _fmt_pct(value)
    if metric["format"] == "pp":
        return f"{value * 100:+.1f} pp"
    if metric["format"] == "x":
        return _fmt_x(value)
    return f"{value:.2f}"


def _recommendation(
    *,
    ticker: str,
    scores: dict[str, Optional[float]],
    composite: Optional[float],
    coverage: float,
    flags: list[dict[str, str]],
    owned: bool,
    position: Optional[dict[str, Any]],
    price: Optional[float],
    valuation: dict[str, Any],
    margin_of_safety: Optional[float],
    entry_price: Optional[float],
    trim_price: Optional[float],
    is_financial: bool,
    metric_groups: dict[str, list[dict[str, Any]]],
    assumptions: ValuationAssumptions,
    roic: Optional[float],
    wacc: Optional[float],
    revenue_cagr: Optional[float],
) -> dict[str, Any]:
    quality = scores.get("quality")
    strength = scores.get("strength")
    valuation_score = scores.get("valuation")
    momentum_score = scores.get("momentum")
    severe = [flag for flag in flags if flag["severity"] == "high"]
    rationale: list[str] = []

    if composite is None or coverage < 0.45 or quality is None or valuation_score is None:
        action = "INSUFFICIENT_DATA"
        rationale.append("Not enough statement and price data to score quality and valuation; refresh this ticker first.")
    elif owned:
        overweight = bool(position and position.get("overweight"))
        underweight = bool(position and position.get("underweight"))
        if severe and ((strength is not None and strength < 40) or composite < 45):
            action = "SELL_REVIEW"
            rationale.append("Serious financial red flags combined with a weak overall score: re-underwrite the thesis.")
        elif composite < 40:
            action = "SELL_REVIEW"
            rationale.append("Overall fundamental score is weak; the original thesis may no longer hold.")
        elif margin_of_safety is not None and margin_of_safety < -0.35 and valuation_score < 30:
            action = "TRIM"
            rationale.append("Price is far above estimated fair value; consider taking partial profits.")
        elif overweight:
            action = "TRIM"
            rationale.append("Position is well above its target allocation; rebalance to control concentration risk.")
        elif composite >= 68 and valuation_score >= 60 and not severe:
            action = "ADD"
            rationale.append("High-scoring business still trading below fair value.")
            if underweight:
                rationale.append("Position is below its target allocation.")
        else:
            action = "HOLD"
            rationale.append("Fundamentals support keeping the position; valuation does not argue for adding or trimming.")
    else:
        if severe and ((strength is not None and strength < 40) or composite < 50):
            action = "AVOID"
            rationale.append("Serious financial red flags outweigh the opportunity.")
        elif composite < 40:
            action = "AVOID"
            rationale.append("Low overall score across quality, strength, growth and valuation.")
        elif composite >= 68 and valuation_score >= 60 and not severe:
            action = "BUY"
            rationale.append("Strong business at a price below estimated fair value.")
        elif composite >= 58 and quality >= 60 and not severe and valuation_score >= 45:
            action = "ACCUMULATE"
            rationale.append("Good business at a roughly fair price: build a position gradually rather than all at once.")
        else:
            action = "WATCH"
            if quality >= 60 and valuation_score < 45:
                rationale.append("Good business, but the price already reflects it; wait for a better entry.")
            else:
                rationale.append("Mixed fundamentals; no compelling reason to buy yet.")

    # Conviction: data coverage, agreement between pillars, and how much the call rests on a model.
    present = [score for score in scores.values() if score is not None]
    dispersion = pstdev(present) if len(present) >= 3 else 30
    if action == "INSUFFICIENT_DATA":
        conviction = "low"
    elif coverage >= 0.85 and dispersion < 20 and valuation.get("applicable"):
        conviction = "high"
    elif coverage >= 0.65:
        conviction = "medium"
    else:
        conviction = "low"
    if conviction == "high" and (is_financial or (valuation.get("terminal_value_share") or 0) > 0.8):
        conviction = "medium"

    # Narrative.
    summary_parts = [
        _bucket(quality, ("a highly profitable, high-quality business", "solid profitability", "average profitability", "weak profitability")),
        _bucket(strength, ("a fortress balance sheet", "a healthy balance sheet", "some balance-sheet strain", "a stretched balance sheet")),
        _bucket(scores.get("growth"), ("strong growth", "steady growth", "slow growth", "stalling or shrinking sales")),
    ]
    if not any(summary_parts):
        summary = f"Not enough data to assess {ticker} yet"
    else:
        summary = f"{ticker} shows " + ", ".join(part for part in summary_parts if part)
    base_value = valuation.get("base_value")
    if base_value and price is not None and margin_of_safety is not None:
        relation = "discount" if margin_of_safety >= 0 else "premium"
        summary += f". At {_fmt_money(price)} it trades at a {abs(margin_of_safety) * 100:.0f}% {relation} to an estimated fair value of {_fmt_money(base_value)}"
        summary += f" (range {_fmt_money(valuation.get('bear_value'))} - {_fmt_money(valuation.get('bull_value'))})."
    else:
        summary += "."
    if roic is not None and wacc is not None:
        spread = roic - wacc
        summary += (
            f" ROIC of {_fmt_pct(roic)} {'exceeds' if spread > 0 else 'trails'} its {_fmt_pct(wacc)} cost of capital, "
            f"so growth {'creates' if spread > 0 else 'destroys'} value."
        )

    strengths: list[tuple[float, str]] = []
    concerns: list[tuple[float, str]] = []
    for group in metric_groups.values():
        for metric in group:
            if metric["score"] is None:
                continue
            text = f"{metric['label']}: {_format_metric(metric)}" + (f" ({metric['note']})" if metric["note"] and metric["value"] is not None else "")
            if metric["score"] >= 75:
                strengths.append((metric["score"] * metric["weight"], text))
            elif metric["score"] <= 30:
                concerns.append((-metric["score"] * metric["weight"] + 100, text))
    strengths_text = [text for _, text in sorted(strengths, reverse=True)[:6]]
    concerns_text = [flag["message"] for flag in flags if flag["severity"] == "high"]
    concerns_text += [text for _, text in sorted(concerns, reverse=True)[: max(0, 6 - len(concerns_text))]]

    triggers: list[str] = []
    required = assumptions.required_margin_of_safety
    if entry_price and action in {"WATCH", "ACCUMULATE", "HOLD"} and price is not None and price > entry_price:
        triggers.append(f"Becomes a stronger buy below {_fmt_money(entry_price)} ({required * 100:.0f}% margin of safety to fair value).")
    if trim_price and action in {"BUY", "ADD", "HOLD", "ACCUMULATE"}:
        triggers.append(f"Valuation gets stretched above {_fmt_money(trim_price)}; reassess or trim there.")
    if valuation.get("implied_growth") is not None:
        triggers.append(
            f"Reverse DCF: the current price implies {_fmt_pct(valuation['implied_growth'])} annual cash-flow growth for 5 years"
            + (f" vs {_fmt_pct(revenue_cagr)} historical revenue CAGR." if revenue_cagr is not None else ".")
        )
    if action in {"BUY", "ADD", "ACCUMULATE", "HOLD"}:
        triggers.append("Downgrade if the Piotroski F-score drops to 3 or below, revenue growth turns negative, or a high-severity red flag appears.")
    if action in {"WATCH", "AVOID"} and (quality or 0) < 60:
        triggers.append("Upgrade if margins and return on capital improve for two consecutive reports.")

    timing_note = None
    if action in {"BUY", "ADD", "ACCUMULATE"} and momentum_score is not None and momentum_score < 40:
        timing_note = "Fundamentals support buying but the price trend is weak: stage entries (for example in thirds) instead of buying all at once."
    elif action in {"BUY", "ADD"} and momentum_score is not None and momentum_score >= 70:
        timing_note = "Price trend is strong; fundamentals and momentum agree."
    elif action in {"TRIM", "SELL_REVIEW"} and position and (position.get("unrealized_return") or 0) > 0.2:
        timing_note = "Consider the tax impact of realizing gains; trimming in stages also limits regret if the stock keeps rising."

    return {
        "action": action,
        "conviction": conviction,
        "composite_score": composite,
        "summary": summary,
        "rationale": rationale,
        "strengths": strengths_text,
        "concerns": concerns_text,
        "triggers": triggers,
        "timing_note": timing_note,
    }
