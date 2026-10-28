"""Scan pipeline: watchlist -> dipping stocks -> rich puts to sell (+ advice on open short puts)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from options.config import IbkrConfig, OptionsConfig
from options.dips import DipMetrics, evaluate_dip
from options.positions import PositionAdvice, ShortPut, advise
from options.puts import PutCandidate, rank_candidates, select_puts
from shared.moomoo import DailyBar, UnderlyingVol

ET = ZoneInfo("America/New_York")


@dataclass
class ScanResult:
    generated_at: datetime
    ranking_metric: str
    universe: list[str] = field(default_factory=list)
    dips: list[DipMetrics] = field(default_factory=list)
    candidates: list[PutCandidate] = field(default_factory=list)
    vols: dict[str, UnderlyingVol] = field(default_factory=dict)
    positions: list[PositionAdvice] = field(default_factory=list)
    positions_status: str = "not requested"
    exposure: dict[str, dict[str, float]] = field(default_factory=dict)
    rejections: dict[str, Counter[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def dipping(self) -> list[DipMetrics]:
        return [item for item in self.dips if item.is_dip]


def run_scan(
    config: OptionsConfig,
    client: Any,
    now: Optional[datetime] = None,
    fetch_positions: Optional[Callable[[IbkrConfig], list[ShortPut]]] = None,
) -> ScanResult:
    now = now or datetime.now(ET)
    today = now.date()
    result = ScanResult(generated_at=now, ranking_metric=config.ranking.metric)

    result.universe = _universe(config, client, result)
    snapshots = _safe(result, "stock snapshots", lambda: client.stock_snapshots(result.universe), {})

    history_start = today - timedelta(days=_history_calendar_days(config))
    for ticker in result.universe:
        try:
            bars = client.daily_bars(ticker, history_start)
            snap = snapshots.get(ticker, {})
            bars = merge_live_price(bars, snap.get("last_price"), snap.get("update_time"))
            result.dips.append(evaluate_dip(ticker, bars, config.dip, prev_close=snap.get("prev_close")))
        except Exception as exc:
            result.errors[ticker] = f"history: {exc}"
    result.dips.sort(key=lambda item: (not item.is_dip, -(item.off_high_pct or 0)))

    dipping = [item.ticker for item in result.dipping]
    if dipping:
        result.vols = _safe(result, "IV overview", lambda: client.underlying_vol(dipping), {})

    all_candidates: list[PutCandidate] = []
    moomoo_delta_seen = False
    quotes_seen = False
    for metrics in result.dipping:
        ticker = metrics.ticker
        try:
            contracts = client.put_chain_codes(
                ticker, today + timedelta(days=config.puts.min_dte), today + timedelta(days=config.puts.max_dte)
            )
            # Chains list every put; keep OTM strikes within range before paying for quotes.
            floor = metrics.spot * (1 - config.puts.max_otm_pct)
            contracts = [item for item in contracts if floor <= item[2] < metrics.spot]
            quotes = client.option_quotes(ticker, contracts) if contracts else []
        except Exception as exc:
            result.errors[ticker] = f"options: {exc}"
            continue
        quotes_seen = quotes_seen or bool(quotes)
        moomoo_delta_seen = moomoo_delta_seen or any(quote.delta is not None for quote in quotes)
        candidates, rejected = select_puts(
            metrics.spot, quotes, config.puts, today, result.vols.get(ticker), config.sizing
        )
        result.rejections[ticker] = rejected
        all_candidates.extend(candidates)
    if quotes_seen and not moomoo_delta_seen:
        result.warnings.append(
            "Moomoo returned option quotes without greeks; delta/IV were estimated with Black-Scholes. "
            "Run `python -m options.cli check` to review your option quote permissions."
        )
    result.candidates = rank_candidates(all_candidates, config.ranking, config.puts.top_per_ticker)

    if config.ibkr.enabled and fetch_positions is not None:
        _add_positions(config, client, result, fetch_positions, snapshots, today)
    return result


def run_positions(
    config: OptionsConfig,
    client: Any,
    fetch_positions: Callable[[IbkrConfig], list[ShortPut]],
    now: Optional[datetime] = None,
) -> ScanResult:
    """Only the IBKR short-put management part of a scan."""
    now = now or datetime.now(ET)
    result = ScanResult(generated_at=now, ranking_metric=config.ranking.metric)
    _add_positions(config, client, result, fetch_positions, {}, now.date())
    return result


def merge_live_price(bars: list[DailyBar], last_price: Optional[float], update_time: Optional[str]) -> list[DailyBar]:
    """Make the last bar reflect the live snapshot price (replace today's bar or append a new one)."""
    if not last_price:
        return bars
    live_date = _date_or_none(update_time)
    if bars and (live_date is None or live_date == bars[-1].date):
        last = bars[-1]
        return bars[:-1] + [DailyBar(date=last.date, high=max(last.high, last_price), close=last_price)]
    if bars and live_date is not None and live_date < bars[-1].date:
        return bars
    return bars + [DailyBar(date=live_date or date.today(), high=last_price, close=last_price)]


def _universe(config: OptionsConfig, client: Any, result: ScanResult) -> list[str]:
    tickers: set[str] = set(config.watchlist.extra_tickers)
    groups = _safe(result, "watchlist", lambda: client.watchlist_tickers(config.watchlist.groups), {})
    for group_tickers in groups.values():
        tickers.update(ticker.upper() for ticker in group_tickers)
    tickers.difference_update(config.watchlist.exclude_tickers)
    if not tickers:
        result.warnings.append("Universe is empty: check watchlist.groups / extra_tickers in options/config.yaml.")
    return sorted(tickers)


def _add_positions(
    config: OptionsConfig,
    client: Any,
    result: ScanResult,
    fetch_positions: Callable[[IbkrConfig], list[ShortPut]],
    snapshots: dict[str, dict[str, Any]],
    today: date,
) -> None:
    try:
        short_puts = fetch_positions(config.ibkr)
    except Exception as exc:
        result.positions_status = f"unavailable: {exc}"
        return
    result.positions_status = f"{len(short_puts)} short put position(s) from IBKR"
    for item in short_puts:
        exposure = result.exposure.setdefault(item.ticker, {"contracts": 0, "collateral": 0.0})
        exposure["contracts"] += item.contracts
        exposure["collateral"] += item.strike * item.multiplier * item.contracts

    missing = sorted({item.ticker for item in short_puts} - set(snapshots))
    if missing:
        snapshots = {**snapshots, **_safe(result, "position snapshots", lambda: client.stock_snapshots(missing), {})}

    quotes: dict[tuple[str, date, float], Any] = {}
    for ticker, expiry in sorted({(item.ticker, item.expiry) for item in short_puts}):
        try:
            contracts = client.put_chain_codes(ticker, expiry, expiry)
            wanted = {item.strike for item in short_puts if item.ticker == ticker and item.expiry == expiry}
            contracts = [entry for entry in contracts if any(abs(entry[2] - strike) < 1e-6 for strike in wanted)]
            for quote in client.option_quotes(ticker, contracts):
                quotes[(ticker, expiry, quote.strike)] = quote
        except Exception as exc:
            result.warnings.append(f"Could not price {ticker} {expiry} puts: {exc}")

    for item in short_puts:
        quote = next(
            (value for key, value in quotes.items() if key[:2] == (item.ticker, item.expiry) and abs(key[2] - item.strike) < 1e-6),
            None,
        )
        mid = None
        if quote is not None and quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid:
            mid = (quote.bid + quote.ask) / 2
        result.positions.append(
            advise(
                item,
                today,
                config.management,
                spot=snapshots.get(item.ticker, {}).get("last_price"),
                option_mid=mid,
                delta=quote.delta if quote is not None else None,
            )
        )


def _history_calendar_days(config: OptionsConfig) -> int:
    """Calendar days of history covering the longest trading-day lookback in use."""
    checks = config.dip.checks
    needed = [checks.off_high.lookback_days, checks.drop_n_days.days + 1, checks.rsi.period * 6]
    if config.dip.require_above_ma.enabled:
        needed.append(config.dip.require_above_ma.days)
    return int((max(needed) + 5) * 1.5) + 10


def _safe(result: ScanResult, label: str, func: Callable[[], Any], default: Any) -> Any:
    try:
        return func()
    except Exception as exc:
        result.warnings.append(f"{label} failed: {exc}")
        return default


def _date_or_none(value: Optional[str]) -> Optional[date]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date() if value else None
    except ValueError:
        return None
