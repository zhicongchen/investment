"""Put-selling signal CLI.

    python -m options.cli check       # verify OpenD / quote permissions / TWS
    python -m options.cli groups      # list Moomoo watchlist groups
    python -m options.cli scan        # one scan -> reports/options/
    python -m options.cli positions   # advice on open IBKR short puts only
    python -m options.cli loop        # scan every N minutes during US market hours
    python -m options.cli history     # recent scans stored in data/options.db

Without OpenD, scan Yahoo Finance data instead (delayed quotes, no IV rank):
    python -m options.cli scan --source yahoo --tickers AAPL MSFT NVDA --all-tickers --no-positions
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from options.config import OptionsConfig, load_config
from options.fundamentals import fundamentals_lookup
from options.positions import fetch_short_puts
from options.report import to_console, write_reports
from options.scanner import ET, run_positions, run_scan
from options.store import CachedMoomooClient, OptionsStore
from options.yahoo import YahooOptionsClient, next_earnings_date
from shared.ibkr import IbkrConnectionError
from shared.moomoo import MoomooClient, MoomooError


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Moomoo put-selling signal scanner (signals only, no orders)")
    parser.add_argument("command", choices=["check", "groups", "scan", "positions", "loop", "history"])
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument("--no-positions", action="store_true", help="skip reading IBKR positions")
    parser.add_argument("--interval", type=int, default=None, help="loop interval in minutes")
    parser.add_argument("--any-time", action="store_true", help="loop even outside US market hours")
    parser.add_argument("--refresh-cache", action="store_true", help="re-download daily bars and option chains")
    parser.add_argument("--limit", type=int, default=20, help="rows for the history command")
    parser.add_argument("--source", choices=["moomoo", "yahoo"], default="moomoo", help="market data for scan/loop")
    parser.add_argument("--tickers", nargs="+", default=None, help="scan these tickers (added to the watchlist universe)")
    parser.add_argument("--all-tickers", action="store_true", help="look for puts on every ticker, not only dipping ones")
    parser.add_argument(
        "--fundamentals", choices=["dashboard", "live", "off"], default=None, help="where verdicts for the underlyings come from"
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    config = load_config(args.config)
    if args.no_positions:
        config.ibkr.enabled = False
    if args.tickers:
        config.watchlist.extra_tickers = sorted({*config.watchlist.extra_tickers, *(t.strip().upper() for t in args.tickers)})
    if args.all_tickers:
        config.dip.required = False
    if args.fundamentals:
        config.edge.fundamentals = args.fundamentals
    try:
        if args.command == "check":
            return check(config)
        if args.command == "groups":
            return groups(config)
        if args.command == "scan":
            return scan(config, refresh_cache=args.refresh_cache, source_name=args.source)
        if args.command == "positions":
            return positions(config)
        if args.command == "history":
            return history(config, args.limit)
        return loop(
            config,
            args.interval or config.loop.interval_minutes,
            market_hours_only=config.loop.market_hours_only and not args.any_time,
            refresh_cache=args.refresh_cache,
            source_name=args.source,
        )
    except MoomooError as exc:
        print(f"Moomoo error: {exc}", file=sys.stderr)
        return 2


def scan(
    config: OptionsConfig, client: Optional[object] = None, refresh_cache: bool = False, source_name: str = "moomoo"
) -> int:
    owned = client is None
    client = client or _client(config, source_name)
    store = OptionsStore(config.storage.db_path)
    source = _data_source(config, client, store, refresh_cache)
    try:
        client.connect()
        result = run_scan(
            config,
            source,
            fetch_positions=fetch_short_puts,
            earnings=getattr(client, "next_earnings", None) or next_earnings_date,
            fundamentals=fundamentals_lookup(config.edge.fundamentals) if config.edge.enabled else None,
        )
    finally:
        if owned:
            client.close()
    if getattr(client, "used_last_trade", False):
        result.warnings.append(
            "Market closed or quotes missing: some option prices are last trades, not live bid/ask. Re-check before trading."
        )
    store.save_scan(result)
    paths = write_reports(result, config.report.path)
    print(to_console(result))
    if isinstance(source, CachedMoomooClient):
        print(f"  cache: {source.hits} reused, {source.misses} fetched (daily bars + chain listings)")
    note = "" if "markdown" in paths else " (signals unchanged since last scan)"
    print(f"report: {paths['latest_markdown']}{note}")
    return 0


def _client(config: OptionsConfig, source_name: str) -> object:
    if source_name == "yahoo":
        return YahooOptionsClient()
    return MoomooClient(config.opend.host, config.opend.port)


def _data_source(config: OptionsConfig, client: object, store: OptionsStore, refresh_cache: bool) -> object:
    if not config.storage.cache_enabled:
        return client
    return CachedMoomooClient(client, store, refresh=refresh_cache)


def history(config: OptionsConfig, limit: int) -> int:
    rows = OptionsStore(config.storage.db_path).recent_scans(limit)
    if not rows:
        print("No scans stored yet. Run: python -m options.cli scan")
        return 0
    print(f"{'ID':>5} {'GENERATED (ET)':<17} {'RANK BY':<17} {'UNIV':>4} {'DIPS':>4} {'PUTS':>4}  TOP PUT")
    for row in rows:
        top = "-"
        if row["top_ticker"]:
            top = (
                f"{row['top_ticker']} {row['top_strike']:g}P {row['top_expiry']} "
                f"@ {row['top_premium']:.2f} ({row['top_yield']:.1%} ann.)"
            )
        print(
            f"{row['id']:>5} {row['generated_at'][:16].replace('T', ' '):<17} {row['ranking_metric']:<17} "
            f"{row['universe_size']:>4} {row['dipping']:>4} {row['candidates']:>4}  {top}"
        )
    print(f"database: {config.storage.db_path}")
    return 0


def positions(config: OptionsConfig) -> int:
    with MoomooClient(config.opend.host, config.opend.port) as client:
        source = _data_source(config, client, OptionsStore(config.storage.db_path), refresh_cache=False)
        result = run_positions(config, source, fetch_short_puts)
    print(to_console(result))
    print(result.positions_status)
    return 0


def groups(config: OptionsConfig) -> int:
    with MoomooClient(config.opend.host, config.opend.port) as client:
        for name, kind in client.watchlist_groups():
            try:
                tickers = client.watchlist_tickers([name])[name]
            except MoomooError as exc:
                print(f"{name:<24} [{kind}] error: {exc}")
                continue
            preview = ", ".join(tickers[:12]) + (" ..." if len(tickers) > 12 else "")
            print(f"{name:<24} [{kind}] {len(tickers):>3} US stocks/ETFs  {preview}")
    return 0


def check(config: OptionsConfig) -> int:
    ok = True
    print(f"OpenD {config.opend.host}:{config.opend.port}")
    with MoomooClient(config.opend.host, config.opend.port) as client:
        state = client.global_state()
        print(f"  connected. login={state.get('qot_logined')} US market={state.get('market_us')}")
        try:
            used, remaining = client.history_quota()
            print(f"  daily kline quota: {used} used, {remaining} remaining (30-day rolling, per distinct stock)")
        except MoomooError as exc:
            print(f"  daily kline quota: {exc}")
        snap = client.stock_snapshots(["AAPL"]).get("AAPL", {})
        print(f"  AAPL snapshot: last={snap.get('last_price')} at {snap.get('update_time')}")
        try:
            vol = client.underlying_vol(["AAPL"]).get("AAPL")
            print(f"  AAPL IV overview: iv={vol.iv if vol else None} iv_rank={vol.iv_rank if vol else None} hv30={vol.hv_30d if vol else None}")
        except MoomooError as exc:
            ok = False
            print(f"  AAPL IV overview FAILED: {exc}")
        today = datetime.now(ET).date()
        chain = client.put_chain_codes("AAPL", today + timedelta(days=7), today + timedelta(days=36))
        print(f"  AAPL puts 7-36 DTE: {len(chain)} contracts")
        if chain and snap.get("last_price"):
            near = sorted(chain, key=lambda item: abs(item[2] - snap["last_price"] * 0.95))[:5]
            quotes = client.option_quotes("AAPL", near)
            with_greeks = sum(1 for quote in quotes if quote.delta is not None and quote.iv is not None)
            with_market = sum(1 for quote in quotes if quote.bid and quote.ask)
            for quote in quotes:
                print(f"    {quote.code:<24} bid={quote.bid} ask={quote.ask} delta={quote.delta} iv={quote.iv} oi={quote.open_interest}")
            if with_market == 0:
                ok = False
                print("  -> no bid/ask on option quotes: your account likely lacks US option (OPRA) quote permission.")
            elif with_greeks == 0:
                print("  -> quotes OK but no greeks/IV: the scanner will estimate delta with Black-Scholes.")
            else:
                print("  -> option quotes with greeks OK.")
    if config.ibkr.enabled:
        print(f"TWS {config.ibkr.host}:{config.ibkr.port} (client id {config.ibkr.client_id}, read-only)")
        try:
            puts = fetch_short_puts(config.ibkr)
            print(f"  connected. {len(puts)} short put position(s)")
        except IbkrConnectionError as exc:
            print(f"  {exc}  (scans still run; position advice is skipped)")
    return 0 if ok else 1


def loop(
    config: OptionsConfig,
    interval_minutes: int,
    market_hours_only: bool = True,
    refresh_cache: bool = False,
    source_name: str = "moomoo",
) -> int:
    print(f"Scanning every {interval_minutes} min" + (" during US market hours" if market_hours_only else "") + ". Ctrl+C to stop.")
    client = _client(config, source_name)
    try:
        while True:
            now = datetime.now(ET)
            if not market_hours_only or is_market_hours(now):
                try:
                    scan(config, client, refresh_cache=refresh_cache)
                    refresh_cache = False  # only the first scan bypasses the cache
                except Exception as exc:  # keep looping through transient failures
                    print(f"[{now:%H:%M}] scan failed: {exc}", file=sys.stderr)
                    client.close()
            else:
                print(f"[{now:%Y-%m-%d %H:%M} ET] market closed, waiting")
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        return 0
    finally:
        client.close()


def is_market_hours(now: datetime) -> bool:
    """Regular US session, Mon-Fri 9:30-16:00 ET. Exchange holidays are not modeled."""
    now = now.astimezone(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


if __name__ == "__main__":
    raise SystemExit(main())
