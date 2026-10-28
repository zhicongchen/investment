"""Moomoo-compatible market data from Yahoo Finance (yfinance), for scanning without OpenD.

Covers what the scanner needs: snapshots, daily bars, put chains with bid/ask/open interest,
an IV overview (at-the-money IV and 30-day realized volatility; Yahoo has no IV rank) and the
next earnings date. Yahoo option quotes are delayed about 15 minutes; outside market hours
bid/ask can be empty, in which case the last trade is used and `used_last_trade` is set so the
report can warn.

Yahoo's own implied volatilities are not used: outside market hours they are placeholders
(6.25%, 12.5%, ...) that make every put look riskless. IV is solved from the option price
instead, so delta, probability of profit and IV/RV all match the premium being collected.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from options.edge import realized_vol
from options.pricing import implied_vol

RISK_FREE_RATE = 0.04
from shared.moomoo import DailyBar, OptionQuote, UnderlyingVol


def _num(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _int(value: Any) -> Optional[int]:
    parsed = _num(value)
    return int(parsed) if parsed is not None else None


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        try:
            return value.date()
        except Exception:
            return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class YahooOptionsClient:
    def __init__(self, yf: Any = None) -> None:
        if yf is None:
            import yfinance as yf  # imported lazily so Moomoo-only installs never need it
        self._yf = yf
        self._tickers: dict[str, Any] = {}
        self._chains: dict[tuple[str, date], Any] = {}
        self.used_last_trade = False

    # The scanner and CLI treat this like a MoomooClient. Each scan calls connect(), which drops the
    # previous scan's chains so a loop never reuses stale quotes.
    def connect(self) -> None:
        self._tickers.clear()
        self._chains.clear()
        self.used_last_trade = False

    def close(self) -> None:
        return None

    def __enter__(self) -> "YahooOptionsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def _ticker(self, symbol: str) -> Any:
        if symbol not in self._tickers:
            self._tickers[symbol] = self._yf.Ticker(symbol)
        return self._tickers[symbol]

    def watchlist_tickers(self, groups: Iterable[str]) -> dict[str, list[str]]:
        return {}  # no watchlists on Yahoo: the universe comes from extra_tickers / --tickers

    def stock_snapshots(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for ticker in tickers:
            history = self._ticker(ticker).history(period="5d", auto_adjust=False)
            if history is None or history.empty:
                continue
            closes = [_num(value) for value in history["Close"].tolist()]
            closes = [value for value in closes if value]
            if not closes:
                continue
            result[ticker] = {
                "last_price": closes[-1],
                "prev_close": closes[-2] if len(closes) >= 2 else None,
                "update_time": str(history.index[-1])[:19],
            }
        return result

    def daily_bars(self, ticker: str, start: date, end: Optional[date] = None, adjusted: bool = True) -> list[DailyBar]:
        history = self._ticker(ticker).history(
            start=start.isoformat(), end=(end or date.today() + timedelta(days=1)).isoformat(), auto_adjust=adjusted
        )
        bars: list[DailyBar] = []
        if history is None or history.empty:
            return bars
        for index, row in history.iterrows():
            close, high = _num(row.get("Close")), _num(row.get("High"))
            if close is None:
                continue
            bars.append(
                DailyBar(
                    date=_as_date(index) or date.today(),
                    high=high if high is not None else close,
                    close=close,
                    open=_num(row.get("Open")),
                    low=_num(row.get("Low")),
                    volume=_num(row.get("Volume")),
                )
            )
        return bars

    def underlying_vol(self, tickers: list[str]) -> dict[str, UnderlyingVol]:
        result: dict[str, UnderlyingVol] = {}
        today = date.today()
        for ticker in tickers:
            bars = self.daily_bars(ticker, today - timedelta(days=70))
            closes = [bar.close for bar in bars]
            hv = realized_vol(closes, 30)
            atm_iv = None
            if closes:
                atm_iv = self._atm_iv(ticker, closes[-1], today)
            result[ticker] = UnderlyingVol(iv=atm_iv, hv_30d=hv)
        return result

    def _atm_iv(self, ticker: str, spot: float, today: date) -> Optional[float]:
        """Average IV of the two puts nearest the money in the expiry closest to 30 days out."""
        expiries = [expiry for expiry in self._expiries(ticker) if expiry > today]
        if not expiries:
            return None
        target = min(expiries, key=lambda expiry: abs((expiry - today).days - 30))
        frame = self._chain(ticker, target)
        if frame is None or frame.empty:
            return None
        rows = sorted(frame.to_dict("records"), key=lambda row: abs((_num(row.get("strike")) or 0) - spot))[:2]
        years = max((target - today).days, 1) / 365.0
        ivs = []
        for row in rows:
            price = _quote_price(row)
            strike = _num(row.get("strike"))
            if price and strike:
                iv = implied_vol(price, spot, strike, years, RISK_FREE_RATE)
                if iv:
                    ivs.append(iv)
        return sum(ivs) / len(ivs) if ivs else None

    def _expiries(self, ticker: str) -> list[date]:
        try:
            raw = self._ticker(ticker).options or ()
        except Exception:
            return []
        return [parsed for parsed in (_as_date(value) for value in raw) if parsed]

    def _chain(self, ticker: str, expiry: date) -> Any:
        key = (ticker, expiry)
        if key not in self._chains:
            self._chains[key] = self._ticker(ticker).option_chain(expiry.isoformat()).puts
        return self._chains[key]

    def put_chain_codes(self, ticker: str, first_expiry: date, last_expiry: date) -> list[tuple[str, date, float]]:
        found: list[tuple[str, date, float]] = []
        for expiry in self._expiries(ticker):
            if not first_expiry <= expiry <= last_expiry:
                continue
            frame = self._chain(ticker, expiry)
            for row in frame.to_dict("records") if frame is not None else []:
                strike = _num(row.get("strike"))
                if strike is not None and row.get("contractSymbol"):
                    found.append((str(row["contractSymbol"]), expiry, strike))
        return found

    def option_quotes(self, ticker: str, contracts: list[tuple[str, date, float]]) -> list[OptionQuote]:
        wanted = {code for code, _, _ in contracts}
        quotes: list[OptionQuote] = []
        for expiry in sorted({expiry for _, expiry, _ in contracts}):
            frame = self._chain(ticker, expiry)
            for row in frame.to_dict("records") if frame is not None else []:
                code = str(row.get("contractSymbol"))
                if code not in wanted:
                    continue
                bid, ask, last = _num(row.get("bid")), _num(row.get("ask")), _num(row.get("lastPrice"))
                if not bid or not ask:
                    if not last:
                        bid = ask = None
                    else:
                        bid = ask = last  # market closed: last trade stands in for the quote
                        self.used_last_trade = True
                quotes.append(
                    OptionQuote(
                        code=code,
                        ticker=ticker,
                        expiry=expiry,
                        strike=float(row["strike"]),
                        bid=bid,
                        ask=ask,
                        last=last,
                        iv=None,  # solved from the price by the scanner; see module docstring
                        open_interest=_int(row.get("openInterest")),
                        volume=_int(row.get("volume")),
                    )
                )
        return quotes

    def next_earnings(self, ticker: str) -> Optional[date]:
        return next_earnings_date(ticker, self._ticker(ticker))


def _quote_price(row: dict[str, Any]) -> Optional[float]:
    bid, ask = _num(row.get("bid")), _num(row.get("ask"))
    if bid and ask and ask >= bid:
        return (bid + ask) / 2
    return _num(row.get("lastPrice")) or None


def next_earnings_date(ticker: str, stock: Any = None, today: Optional[date] = None) -> Optional[date]:
    """Next scheduled earnings date from Yahoo, or None when unknown."""
    today = today or date.today()
    if stock is None:
        import yfinance as yf

        stock = yf.Ticker(ticker)
    candidates: list[date] = []
    try:
        calendar = stock.calendar or {}
        raw = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
        for value in raw if isinstance(raw, (list, tuple)) else [raw]:
            parsed = _as_date(value)
            if parsed:
                candidates.append(parsed)
    except Exception:
        pass
    if not candidates:
        try:
            frame = stock.get_earnings_dates(limit=8)
            candidates = [parsed for parsed in (_as_date(value) for value in frame.index) if parsed] if frame is not None else []
        except Exception:
            pass
    upcoming = sorted(day for day in candidates if day >= today)
    return upcoming[0] if upcoming else None
