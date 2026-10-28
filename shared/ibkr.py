"""IBKR TWS connection helpers shared by the trading bot and the options scanner."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from ib_async import IB
except ImportError:  # pragma: no cover
    IB = None


class IbkrConnectionError(RuntimeError):
    pass


def ensure_event_loop() -> None:
    # ib_async needs an event loop on the calling thread.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def error_message(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def connect(ib: Any, host: str, port: int, client_id: int, *, readonly: bool, timeout: float = 15, **kwargs: Any) -> None:
    ensure_event_loop()
    ib.connect(host, port, clientId=client_id, readonly=readonly, timeout=timeout, **kwargs)


@contextmanager
def session(host: str, port: int, client_id: int, *, readonly: bool = True) -> Iterator[Any]:
    """Connected IB instance for the duration of the block; always disconnects."""
    if IB is None:
        raise IbkrConnectionError("ib_async is not installed: pip install ib_async")
    ib = IB()
    try:
        connect(ib, host, port, client_id, readonly=readonly)
    except Exception as exc:
        raise IbkrConnectionError(f"TWS not reachable at {host}:{port} ({error_message(exc)})") from exc
    try:
        yield ib
    finally:
        ib.disconnect()
