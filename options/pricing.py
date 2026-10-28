"""Black-Scholes helpers used only when Moomoo returns quotes without greeks or IV."""
from __future__ import annotations

import math
from typing import Optional


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _d1_d2(spot: float, strike: float, years: float, rate: float, vol: float) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    return d1, d1 - vol * math.sqrt(years)


def put_price(spot: float, strike: float, years: float, rate: float, vol: float) -> float:
    if years <= 0 or vol <= 0:
        return max(strike - spot, 0.0)
    d1, d2 = _d1_d2(spot, strike, years, rate, vol)
    return strike * math.exp(-rate * years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def put_delta(spot: float, strike: float, years: float, rate: float, vol: float) -> Optional[float]:
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return None
    d1, _ = _d1_d2(spot, strike, years, rate, vol)
    return _norm_cdf(d1) - 1.0


def implied_vol(price: float, spot: float, strike: float, years: float, rate: float) -> Optional[float]:
    """Bisection solve for put IV. Returns None when the price is outside no-arbitrage bounds."""
    if price <= 0 or spot <= 0 or strike <= 0 or years <= 0:
        return None
    intrinsic = max(strike * math.exp(-rate * years) - spot, 0.0)
    if price <= intrinsic or price >= strike:
        return None
    low, high = 1e-4, 5.0
    for _ in range(100):
        mid = (low + high) / 2
        if put_price(spot, strike, years, rate, mid) > price:
            high = mid
        else:
            low = mid
        if high - low < 1e-6:
            break
    return (low + high) / 2
