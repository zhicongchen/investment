"""Yahoo Finance (yfinance) stand-in for MoomooClient, used by the daily GitHub Pages job.

Yahoo has no greeks or IV rank: the scanner estimates delta with Black-Scholes and IV rank stays empty.
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime
from typing import Any, Callable, Optional

from shared.moomoo import DailyBar, OptionQuote, UnderlyingVol

# Yahoo reports placeholder IVs (e.g. 1e-5) for contracts without a market.
MIN_SANE_IV = 0.01
MAX_SANE_IV = 5.0
# How old a last trade may be when Yahoo has no bid/ask and it stands in for both.
LAST_TRADE_MAX_AGE_DAYS = 3


def _ticker_factory() -> Callable[[str], Any]:
    import yfinance as yf

    return yf.Ticker


class YahooClient:
    """Implements the client calls options.scanner.run_scan makes, backed by yfinance."""

    def __init__(
        self,
        today: date,
        bars: Optional[dict[str, list[DailyBar]]] = None,
        ticker_factory: Optional[Callable[[str], Any]] = None,
        pause_seconds: float = 0.5,
        min_dte: int = 7,
    ) -> None:
        self.today = today
        self.bars = bars if bars is not None else {}
        self._ticker_factory = ticker_factory
        self._tickers: dict[str, Any] = {}
        self._chains: dict[tuple[str, date], list[Any]] = {}
        self.pause_seconds = pause_seconds
        self.min_dte = min_dte
        self.last_price_fallbacks = 0

    # ---- calls used by run_scan -------------------------------------------

    def watchlist_tickers(self, groups: Any) -> dict[str, list[str]]:
        # No Yahoo watchlists: the universe comes from config.watchlist.extra_tickers.
        return {}

    def stock_snapshots(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        # Runs after the close, so the last daily bar already is the latest price.
        return {}

    def daily_bars(self, ticker: str, start: date, end: Optional[date] = None, adjusted: bool = True) -> list[DailyBar]:
        if ticker not in self.bars:
            history = self._ticker(ticker).history(start=start.isoformat(), auto_adjust=False)
            self.bars[ticker] = bars_from_history(history)
        return [bar for bar in self.bars[ticker] if bar.date >= start and (end is None or bar.date <= end)]

    def underlying_vol(self, tickers: list[str]) -> dict[str, UnderlyingVol]:
        """IV of the put nearest the money in the first expiry at least min_dte out, plus 30-day HV."""
        result: dict[str, UnderlyingVol] = {}
        for ticker in tickers:
            closes = [bar.close for bar in self.bars.get(ticker, [])]
            iv = None
            expiries = [expiry for expiry in self._expiries(ticker) if (expiry - self.today).days >= self.min_dte]
            if expiries and closes:
                spot = closes[-1]
                quotes = [row for row in self._chain(ticker, expiries[0]) if _sane_iv(row.get("impliedVolatility"))]
                if quotes:
                    nearest = min(quotes, key=lambda row: abs(float(row["strike"]) - spot))
                    iv = _sane_iv(nearest.get("impliedVolatility"))
            result[ticker] = UnderlyingVol(iv=iv, hv_30d=historical_vol(closes, 30))
        return result

    def put_chain_codes(self, ticker: str, first_expiry: date, last_expiry: date) -> list[tuple[str, date, float]]:
        codes: list[tuple[str, date, float]] = []
        for expiry in self._expiries(ticker):
            if first_expiry <= expiry <= last_expiry:
                codes.extend((str(row["contractSymbol"]), expiry, float(row["strike"])) for row in self._chain(ticker, expiry))
        return codes

    def option_quotes(self, ticker: str, contracts: list[tuple[str, date, float]]) -> list[OptionQuote]:
        wanted = {code for code, _, _ in contracts}
        quotes: list[OptionQuote] = []
        for expiry in sorted({expiry for _, expiry, _ in contracts}):
            for row in self._chain(ticker, expiry):
                if row["contractSymbol"] in wanted:
                    quotes.append(self._quote(ticker, expiry, row))
        return quotes

    # ---- helpers ----------------------------------------------------------

    def _quote(self, ticker: str, expiry: date, row: dict[str, Any]) -> OptionQuote:
        bid = _positive(row.get("bid"))
        ask = _positive(row.get("ask"))
        last = _positive(row.get("lastPrice"))
        if (bid is None or ask is None) and last is not None and self._recent(row.get("lastTradeDate")):
            bid = ask = last
            self.last_price_fallbacks += 1
        return OptionQuote(
            code=str(row["contractSymbol"]),
            ticker=ticker,
            expiry=expiry,
            strike=float(row["strike"]),
            bid=bid,
            ask=ask,
            last=last,
            iv=_sane_iv(row.get("impliedVolatility")),
            open_interest=_int(row.get("openInterest")),
            volume=_int(row.get("volume")),
        )

    def _recent(self, value: Any) -> bool:
        try:
            traded = value.date() if hasattr(value, "date") else datetime.fromisoformat(str(value)[:10]).date()
        except (TypeError, ValueError):
            return False
        return (self.today - traded).days <= LAST_TRADE_MAX_AGE_DAYS

    def _ticker(self, ticker: str) -> Any:
        if ticker not in self._tickers:
            factory = self._ticker_factory or _ticker_factory()
            self._tickers[ticker] = factory(ticker)
        return self._tickers[ticker]

    def _expiries(self, ticker: str) -> list[date]:
        return [datetime.strptime(value, "%Y-%m-%d").date() for value in (self._ticker(ticker).options or ())]

    def _chain(self, ticker: str, expiry: date) -> list[dict[str, Any]]:
        key = (ticker, expiry)
        if key not in self._chains:
            time.sleep(self.pause_seconds)
            puts = self._ticker(ticker).option_chain(expiry.isoformat()).puts
            self._chains[key] = puts.to_dict("records")
        return self._chains[key]


def bars_from_history(history: Any) -> list[DailyBar]:
    """Raw (split-adjusted, not dividend-adjusted) daily bars so the spot matches option strikes."""
    bars: list[DailyBar] = []
    if history is None or history.empty:
        return bars
    for index, row in history.iterrows():
        close = _number(row.get("Close"))
        if close is None:
            continue
        high = _number(row.get("High"))
        bars.append(
            DailyBar(
                date=index.date(),
                high=high if high is not None else close,
                close=close,
                open=_number(row.get("Open")),
                low=_number(row.get("Low")),
                volume=_number(row.get("Volume")),
            )
        )
    return bars


def historical_vol(closes: list[float], window: int) -> Optional[float]:
    recent = closes[-(window + 1) :]
    if len(recent) < window + 1:
        return None
    returns = [math.log(recent[index] / recent[index - 1]) for index in range(1, len(recent))]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive(value: Any) -> Optional[float]:
    parsed = _number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _sane_iv(value: Any) -> Optional[float]:
    parsed = _number(value)
    return parsed if parsed is not None and MIN_SANE_IV <= parsed <= MAX_SANE_IV else None


def _int(value: Any) -> Optional[int]:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None
