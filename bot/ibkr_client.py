from __future__ import annotations

import asyncio
from typing import Any, Optional

from bot.config import BotConfig
from bot.types import AccountSnapshot, BrokerStatus, ContractSummary, DailyBar, Fill, MarketSnapshot, OrderIntent, Position, SubmitResult

try:
    from ib_async import IB, LimitOrder, StartupFetchNONE, Stock
except ImportError:  # pragma: no cover
    IB = None
    LimitOrder = None
    StartupFetchNONE = None
    Stock = None


class IbkrClient:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._ib: Any = IB() if IB is not None else None
        self.account: Optional[str] = None
        self.last_message = ""

    def connect(self) -> BrokerStatus:
        _ensure_event_loop()
        if self._ib is None:
            self.last_message = "ib_async is not installed"
            return self.status()
        if not self._ib.isConnected():
            try:
                self._ib.connect(
                    self.config.ibkr_host,
                    self.config.ibkr_port,
                    clientId=self.config.ibkr_client_id,
                    readonly=not self.config.live_trading_enabled,
                    timeout=15,
                    fetchFields=StartupFetchNONE,
                )
            except Exception as exc:
                self.last_message = _error_message(exc)
                return self.status()
        accounts = self._ib.managedAccounts()
        self.account = accounts[0] if accounts else self.account
        self.last_message = "connected" if self._ib.isConnected() else self.last_message
        return self.status()

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    def status(self) -> BrokerStatus:
        return BrokerStatus(connected=bool(self._ib is not None and self._ib.isConnected()), message=self.last_message)

    def account_summary(self) -> AccountSnapshot | None:
        _ensure_event_loop()
        if not self.status().connected:
            return None
        account = self.account or (self._ib.managedAccounts()[0] if self._ib.managedAccounts() else "")
        if not account:
            return None
        values = {}
        for item in self._ib.accountSummary(account):
            if getattr(item, "currency", "USD") in {"USD", ""}:
                values[getattr(item, "tag", "")] = getattr(item, "value", None)
        return AccountSnapshot(
            account=account,
            net_liquidation=_optional_float(values.get("NetLiquidation")),
            buying_power=_optional_float(values.get("BuyingPower")),
        )

    def positions(self) -> list[Position]:
        _ensure_event_loop()
        if not self.status().connected:
            return []
        results: list[Position] = []
        for pos in self._ib.positions():
            contract = pos.contract
            if getattr(contract, "secType", "") != "STK" or getattr(contract, "currency", "") != "USD":
                continue
            results.append(
                Position(
                    account=pos.account,
                    ticker=contract.symbol.upper(),
                    quantity=float(pos.position),
                    average_cost=_optional_float(pos.avgCost),
                    sec_type=contract.secType,
                    exchange=getattr(contract, "exchange", "SMART") or "SMART",
                    currency=contract.currency,
                )
            )
        return results

    def qualify_stock(self, ticker: str) -> ContractSummary:
        _ensure_event_loop()
        if not self.status().connected:
            raise RuntimeError("TWS is not connected")
        if Stock is None:
            raise RuntimeError("ib_async is not installed")
        contract = Stock(ticker.upper(), "SMART", "USD")
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError("Contract qualification failed")
        item = qualified[0]
        return ContractSummary(
            symbol=item.symbol.upper(),
            sec_type=item.secType,
            exchange=item.exchange,
            currency=item.currency,
            con_id=getattr(item, "conId", None),
            primary_exchange=getattr(item, "primaryExchange", None),
        )

    def market_snapshot(self, tickers: list[str]) -> dict[str, MarketSnapshot]:
        history = self.daily_history(tickers)
        snapshots: dict[str, MarketSnapshot] = {}
        for ticker in tickers:
            bars = history.get(ticker.upper(), [])
            snapshots[ticker.upper()] = MarketSnapshot(
                ticker=ticker.upper(),
                last_price=bars[-1].close if bars else None,
                timestamp=bars[-1].date if bars else None,
            )
        return snapshots

    def daily_history(self, tickers: list[str]) -> dict[str, list[DailyBar]]:
        try:
            import yfinance as yf
        except ImportError:
            return {ticker.upper(): [] for ticker in tickers}
        history: dict[str, list[DailyBar]] = {}
        for ticker in tickers:
            symbol = ticker.upper()
            try:
                frame = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
            except Exception:
                history[symbol] = []
                continue
            bars: list[DailyBar] = []
            if "Close" in frame:
                for index, row in frame.dropna(subset=["Close"]).iterrows():
                    bars.append(DailyBar(date=index.to_pydatetime(), close=float(row["Close"])))
            history[symbol] = bars
        return history

    def place_limit_order(self, intent: OrderIntent) -> SubmitResult:
        _ensure_event_loop()
        if not self.status().connected:
            raise RuntimeError("TWS is not connected")
        if Stock is None or LimitOrder is None:
            raise RuntimeError("ib_async is not installed")
        contract = Stock(intent.ticker.upper(), "SMART", "USD")
        order = LimitOrder(intent.side.upper(), intent.quantity, intent.limit_price, tif=intent.time_in_force.upper())
        trade = self._ib.placeOrder(contract, order)
        status = getattr(trade, "orderStatus", None)
        order_obj = getattr(trade, "order", None)
        return SubmitResult(
            order_id=getattr(order_obj, "orderId", None),
            perm_id=getattr(order_obj, "permId", None),
            status=getattr(status, "status", "Submitted") if status else "Submitted",
            filled=float(getattr(status, "filled", 0) or 0) if status else 0,
            remaining=float(getattr(status, "remaining", intent.quantity) or 0) if status else intent.quantity,
            average_fill_price=_optional_float(getattr(status, "avgFillPrice", None)) if status else None,
            raw_message=repr(trade),
        )

    def fills(self) -> list[Fill]:
        # Fill polling is intentionally isolated here; tests can provide a fake client with concrete fills.
        return []


def _ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _optional_float(value: object) -> Optional[float]:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__
