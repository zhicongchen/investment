"""Thin wrapper around the moomoo OpenD quote API with rate limiting and normalized outputs."""
from __future__ import annotations

import socket
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

try:
    import moomoo as ft
except ImportError:  # pragma: no cover
    ft = None


SNAPSHOT_BATCH = 400
OVERVIEW_BATCH = 500
CHAIN_WINDOW_DAYS = 29  # OpenD caps a chain request at a 30-day expiry window


@dataclass(frozen=True)
class DailyBar:
    date: date
    high: float
    close: float
    open: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None


@dataclass(frozen=True)
class OptionQuote:
    code: str
    ticker: str
    expiry: date
    strike: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    delta: Optional[float] = None
    iv: Optional[float] = None  # decimal, 0.35 == 35%
    open_interest: Optional[int] = None
    volume: Optional[int] = None


@dataclass(frozen=True)
class UnderlyingVol:
    iv: Optional[float] = None  # decimal
    iv_rank: Optional[float] = None  # 0-100
    iv_percentile: Optional[float] = None  # 0-100
    hv_30d: Optional[float] = None  # decimal

    @property
    def iv_hv_ratio(self) -> Optional[float]:
        if self.iv and self.hv_30d:
            return self.iv / self.hv_30d
        return None


class MoomooError(RuntimeError):
    pass


class RateLimiter:
    """Allow at most `max_calls` per `period` seconds."""

    def __init__(self, max_calls: int, period: float) -> None:
        self.max_calls = max_calls
        self.period = period
        self.calls: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self.calls and now - self.calls[0] >= self.period:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            time.sleep(self.period - (now - self.calls[0]) + 0.05)
            self.calls.popleft()
        self.calls.append(time.monotonic())


def is_reachable(host: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class MoomooClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 11111) -> None:
        self.host = host
        self.port = port
        self._ctx: Any = None
        # Conservative versions of OpenD's documented per-30s limits.
        self._limits = {
            "chain": RateLimiter(9, 30),
            "snapshot": RateLimiter(55, 30),
            "kline": RateLimiter(55, 30),
            "watchlist": RateLimiter(9, 30),
            "overview": RateLimiter(9, 30),
            "basic": RateLimiter(9, 30),
        }

    def __enter__(self) -> "MoomooClient":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        if self._ctx is not None:
            return
        if ft is None:
            raise MoomooError("moomoo-api is not installed: pip install moomoo-api")
        # OpenQuoteContext retries forever when OpenD is down, so probe the port first.
        if not is_reachable(self.host, self.port):
            raise MoomooError(f"OpenD is not reachable at {self.host}:{self.port}. Start OpenD and log in.")
        ft.SysConfig.enable_console_log(False)
        ft.SysConfig.set_all_thread_daemon(True)
        self._ctx = ft.OpenQuoteContext(host=self.host, port=self.port)

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None

    def _call(self, limiter: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.connect()
        self._limits[limiter].wait()
        result = getattr(self._ctx, method)(*args, **kwargs)
        ret, data = result[0], result[1]
        if ret != ft.RET_OK:
            raise MoomooError(f"{method} failed: {data}")
        return result

    # ---- account-level ---------------------------------------------------

    def global_state(self) -> dict[str, Any]:
        self.connect()
        ret, data = self._ctx.get_global_state()
        if ret != ft.RET_OK:
            raise MoomooError(f"get_global_state failed: {data}")
        return dict(data)

    def history_quota(self) -> tuple[int, int]:
        """(used, remaining) daily-kline quota for the rolling 30 days."""
        self.connect()
        ret, data = self._ctx.get_history_kl_quota(get_detail=False)
        if ret != ft.RET_OK:
            raise MoomooError(f"get_history_kl_quota failed: {data}")
        return int(data[0]), int(data[1])

    def watchlist_groups(self) -> list[tuple[str, str]]:
        _, data = self._call("watchlist", "get_user_security_group", group_type=ft.UserSecurityGroupType.ALL)
        return [(str(row.group_name), str(row.group_type)) for row in data.itertuples()]

    def watchlist_tickers(self, groups: Iterable[str]) -> dict[str, list[str]]:
        """US stock/ETF tickers per watchlist group. Empty `groups` means every group."""
        names = list(groups) or [name for name, _ in self.watchlist_groups()]
        result: dict[str, list[str]] = {}
        allowed_types = {ft.SecurityType.STOCK, ft.SecurityType.ETF}
        for name in names:
            _, data = self._call("watchlist", "get_user_security", name)
            result[name] = [
                code_to_ticker(row.code)
                for row in data.itertuples()
                if str(row.code).startswith("US.") and row.stock_type in allowed_types
            ]
        return result

    # ---- stocks ----------------------------------------------------------

    def stock_basic_info(self, tickers: list[str]) -> dict[str, dict[str, str]]:
        _, data = self._call(
            "basic", "get_stock_basicinfo", ft.Market.US, ft.SecurityType.STOCK, [ticker_to_code(ticker) for ticker in tickers]
        )
        return {
            code_to_ticker(row["code"]): {"name": str(row.get("name") or ""), "exchange": str(row.get("exchange_type") or "")}
            for row in data.to_dict("records")
        }

    def stock_snapshots(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in self._snapshots([ticker_to_code(ticker) for ticker in tickers]):
            result[code_to_ticker(row["code"])] = {
                "last_price": _num(row.get("last_price")),
                "prev_close": _num(row.get("prev_close_price")),
                "update_time": str(row.get("update_time") or ""),
                "pe_ttm": _num(row.get("pe_ttm_ratio"), zero_is_none=True),
                "dividend_yield_ttm": _pct(row.get("dividend_ratio_ttm"), zero_is_none=False),
            }
        return result

    def daily_bars(self, ticker: str, start: date, end: Optional[date] = None, adjusted: bool = True) -> list[DailyBar]:
        """Daily OHLCV from `start` to `end` (default today); forward-adjusted unless `adjusted=False`."""
        bars: list[DailyBar] = []
        page_key = None
        while True:
            _, data, page_key = self._call(
                "kline",
                "request_history_kline",
                ticker_to_code(ticker),
                start=start.isoformat(),
                end=(end or date.today()).isoformat(),
                ktype=ft.KLType.K_DAY,
                autype=ft.AuType.QFQ if adjusted else ft.AuType.NONE,
                max_count=1000,
                page_req_key=page_key,
            )
            for row in data.itertuples():
                bars.append(
                    DailyBar(
                        date=_parse_date(row.time_key),
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume),
                    )
                )
            if page_key is None:
                break
        return bars

    def underlying_vol(self, tickers: list[str]) -> dict[str, UnderlyingVol]:
        result: dict[str, UnderlyingVol] = {}
        codes = [ticker_to_code(ticker) for ticker in tickers]
        for batch in _chunks(codes, OVERVIEW_BATCH):
            _, data = self._call("overview", "get_option_underlying_overview", batch)
            for row in data.to_dict("records"):
                result[code_to_ticker(row["code"])] = UnderlyingVol(
                    iv=_pct(row.get("iv")),
                    iv_rank=_num(row.get("iv_rank")),
                    iv_percentile=_num(row.get("iv_percentile")),
                    hv_30d=_pct(row.get("hv_30d")),
                )
        return result

    # ---- options ---------------------------------------------------------

    def put_chain_codes(self, ticker: str, first_expiry: date, last_expiry: date) -> list[tuple[str, date, float]]:
        """(option code, expiry, strike) for every put expiring in [first_expiry, last_expiry]."""
        found: list[tuple[str, date, float]] = []
        window_start = first_expiry
        while window_start <= last_expiry:
            window_end = min(window_start + timedelta(days=CHAIN_WINDOW_DAYS), last_expiry)
            _, data = self._call(
                "chain",
                "get_option_chain",
                ticker_to_code(ticker),
                start=window_start.isoformat(),
                end=window_end.isoformat(),
                option_type=ft.OptionType.PUT,
            )
            for row in data.itertuples():
                found.append((str(row.code), _parse_date(row.strike_time), float(row.strike_price)))
            window_start = window_end + timedelta(days=1)
        return found

    def option_quotes(self, ticker: str, contracts: list[tuple[str, date, float]]) -> list[OptionQuote]:
        meta = {code: (expiry, strike) for code, expiry, strike in contracts}
        quotes: list[OptionQuote] = []
        for row in self._snapshots(list(meta)):
            expiry, strike = meta[row["code"]]
            quotes.append(
                OptionQuote(
                    code=row["code"],
                    ticker=ticker,
                    expiry=expiry,
                    strike=strike,
                    bid=_num(row.get("bid_price")),
                    ask=_num(row.get("ask_price")),
                    last=_num(row.get("last_price")),
                    delta=_num(row.get("option_delta"), zero_is_none=True),
                    iv=_pct(row.get("option_implied_volatility")),
                    open_interest=_int(row.get("option_open_interest")),
                    volume=_int(row.get("volume")),
                )
            )
        return quotes

    def _snapshots(self, codes: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for batch in _chunks(codes, SNAPSHOT_BATCH):
            _, data = self._call("snapshot", "get_market_snapshot", batch)
            rows.extend(data.to_dict("records"))
        return rows


def ticker_to_code(ticker: str) -> str:
    return ticker if ticker.startswith("US.") else f"US.{ticker.upper()}"


def code_to_ticker(code: str) -> str:
    return code.split(".", 1)[1] if "." in code else code


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _parse_date(value: Any) -> date:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _num(value: Any, zero_is_none: bool = False) -> Optional[float]:
    try:
        if value is None or value == "N/A" or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if zero_is_none and number == 0:
        return None
    return number


def _pct(value: Any, zero_is_none: bool = True) -> Optional[float]:
    """Moomoo reports IV/HV/dividend yield in percent; convert to a decimal (0 means missing for IV/HV)."""
    number = _num(value, zero_is_none=zero_is_none)
    return number / 100.0 if number is not None else None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None else None
