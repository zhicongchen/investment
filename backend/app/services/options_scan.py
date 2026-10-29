"""Run the put-selling scanner (repo-root `options` package) on demand for the dashboard.

Same pipeline as `python -m options.cli scan`: dips -> put chains -> graded puts, with the
dashboard's financial analysis supplying each underlying's verdict and fair value. The result is
saved to the scan history and written to reports/options/latest.*, so the Put Signals tab and the
CLI always show the same latest scan.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from app.config import MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT, OPTIONS_REPORT_DIR
from options.config import OptionsConfig, load_config
from options.edge import Underlying
from options.fundamentals import fundamentals_lookup, to_underlying
from options.report import to_dict, write_reports
from options.scanner import run_scan
from options.store import CachedMoomooClient, OptionsStore
from options.yahoo import YahooOptionsClient, next_earnings_date
from shared.moomoo import MoomooClient, is_reachable

MAX_TICKERS = 30
_scan_lock = threading.Lock()


class ScanBusyError(RuntimeError):
    pass


def scan_defaults(watchlist: list[str]) -> dict[str, Any]:
    """What the dashboard pre-fills: dashboard watchlist plus the scanner's extra tickers."""
    config = load_config()
    tickers = sorted({*watchlist, *config.watchlist.extra_tickers} - set(config.watchlist.exclude_tickers))
    return {
        "tickers": tickers,
        "ranking_metric": config.ranking.metric,
        "min_dte": config.puts.min_dte,
        "max_dte": config.puts.max_dte,
        "delta_min": config.puts.delta_min,
        "delta_max": config.puts.delta_max,
        "earnings": config.edge.earnings,
        "positions_enabled": config.ibkr.enabled,
        "moomoo_available": is_reachable(MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT, timeout=1),
    }


def run_options_scan(
    tickers: list[str],
    source: str = "auto",
    only_dipping: bool = False,
    include_positions: bool = False,
    use_watchlist_groups: bool = False,
    fundamentals: Optional[Callable[[list[str]], dict[str, Underlying]]] = None,
    client: Any = None,
    config: Optional[OptionsConfig] = None,
) -> dict[str, Any]:
    """Run one scan and return it in the same JSON shape as reports/options/latest.json.

    `fundamentals` maps tickers to verdicts (the API passes one backed by its own database);
    `client` overrides the market data source (tests).
    """
    config = config or load_config()
    config.watchlist.extra_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not use_watchlist_groups:
        config.watchlist.groups = []
    config.dip.required = only_dipping
    config.ibkr.enabled = include_positions

    if not _scan_lock.acquire(blocking=False):
        raise ScanBusyError("A put scan is already running; wait for it to finish.")
    try:
        owned = client is None
        source_used = source
        if client is None:
            client, source_used = _client(source)
        if not use_watchlist_groups:
            client = _NoGroups(client)  # an empty group list means "every group" to Moomoo
        store = OptionsStore(config.storage.db_path)
        data = CachedMoomooClient(client, store) if config.storage.cache_enabled and owned else client
        try:
            if owned:
                client.connect()
            result = run_scan(
                config,
                data,
                fetch_positions=_fetch_positions() if include_positions else None,
                earnings=getattr(client, "next_earnings", None) or next_earnings_date,
                fundamentals=fundamentals,
            )
        finally:
            if owned:
                client.close()
        if getattr(client, "used_last_trade", False):
            result.warnings.append(
                "Market closed or quotes missing: some option prices are last trades, not live bid/ask. Re-check before trading."
            )
        if source_used == "yahoo" and source == "auto":
            result.warnings.append("Moomoo OpenD is not running, so this scan used delayed Yahoo Finance data (no IV rank).")
        store.save_scan(result)
        write_reports(result, OPTIONS_REPORT_DIR)
        payload = to_dict(result)
        payload["source"] = source_used
        return payload
    finally:
        _scan_lock.release()


def dashboard_fundamentals(analyze: Callable[[str], Optional[dict[str, Any]]]) -> Callable[[list[str]], dict[str, Underlying]]:
    """Verdicts from the dashboard database; tickers without stored statements are analyzed live in memory."""

    def lookup(tickers: list[str]) -> dict[str, Underlying]:
        views: dict[str, Underlying] = {}
        missing: list[str] = []
        for ticker in tickers:
            analysis = analyze(ticker)
            if analysis is None:
                missing.append(ticker)
            else:
                views[ticker] = to_underlying(analysis)
        live = fundamentals_lookup("live")
        if missing and live is not None:
            views.update(live(missing))
        return views

    return lookup


def _client(source: str) -> tuple[Any, str]:
    if source == "moomoo" or (source == "auto" and is_reachable(MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT, timeout=1)):
        return MoomooClient(MOOMOO_OPEND_HOST, MOOMOO_OPEND_PORT), "moomoo"
    return YahooOptionsClient(), "yahoo"


def _fetch_positions() -> Callable[..., Any]:
    from options.positions import fetch_short_puts  # needs ib_async; only imported when positions are requested

    return fetch_short_puts


class _NoGroups:
    """Scan only the requested tickers, not every Moomoo watchlist group."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def watchlist_tickers(self, groups: Any) -> dict[str, list[str]]:
        return {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
