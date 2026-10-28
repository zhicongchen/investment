"""SQLite storage for the options scanner: a per-day market data cache and the scan history.

Daily bars and option-chain listings only change once a day, so a 30-minute loop fetches
them on the first scan of the day and reuses them afterwards. Live prices (stock and option
snapshots, IV overview) are never cached.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from options.scanner import ET, ScanResult
from shared.moomoo import DailyBar

SCHEMA = """
CREATE TABLE IF NOT EXISTS bar_cache (
    ticker TEXT PRIMARY KEY,
    fetched_on TEXT NOT NULL,
    start TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chain_cache (
    ticker TEXT NOT NULL,
    first_expiry TEXT NOT NULL,
    last_expiry TEXT NOT NULL,
    fetched_on TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (ticker, first_expiry, last_expiry)
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    ranking_metric TEXT NOT NULL,
    universe_size INTEGER NOT NULL,
    dipping INTEGER NOT NULL,
    candidates INTEGER NOT NULL,
    positions_status TEXT,
    warnings TEXT
);
CREATE TABLE IF NOT EXISTS scan_dips (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    ticker TEXT NOT NULL,
    spot REAL,
    day_change_pct REAL,
    off_high_pct REAL,
    n_day_return_pct REAL,
    rsi REAL,
    is_dip INTEGER NOT NULL,
    iv REAL,
    iv_rank REAL,
    iv_hv_ratio REAL
);
CREATE TABLE IF NOT EXISTS scan_candidates (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    rank INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    code TEXT NOT NULL,
    expiry TEXT NOT NULL,
    dte INTEGER NOT NULL,
    strike REAL NOT NULL,
    spot REAL NOT NULL,
    bid REAL,
    ask REAL,
    premium REAL,
    delta REAL,
    delta_estimated INTEGER,
    iv REAL,
    iv_rank REAL,
    iv_hv_ratio REAL,
    open_interest INTEGER,
    otm_pct REAL,
    annualized_yield REAL,
    score REAL
);
CREATE INDEX IF NOT EXISTS idx_scan_candidates_ticker ON scan_candidates(ticker, expiry, strike);
"""


class OptionsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # ---- day cache -------------------------------------------------------

    def cached_bars(self, ticker: str, start: date, today: date) -> Optional[list[DailyBar]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT fetched_on, start, payload FROM bar_cache WHERE ticker = ?", (ticker,)).fetchone()
        if row is None or row[0] != today.isoformat() or row[1] > start.isoformat():
            return None
        return [DailyBar(date=date.fromisoformat(item["date"]), high=item["high"], close=item["close"]) for item in json.loads(row[2])]

    def save_bars(self, ticker: str, start: date, today: date, bars: list[DailyBar]) -> None:
        payload = json.dumps([{"date": bar.date.isoformat(), "high": bar.high, "close": bar.close} for bar in bars])
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO bar_cache (ticker, fetched_on, start, payload) VALUES (?, ?, ?, ?)",
                (ticker, today.isoformat(), start.isoformat(), payload),
            )

    def cached_chain(self, ticker: str, first: date, last: date, today: date) -> Optional[list[tuple[str, date, float]]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT fetched_on, payload FROM chain_cache WHERE ticker = ? AND first_expiry = ? AND last_expiry = ?",
                (ticker, first.isoformat(), last.isoformat()),
            ).fetchone()
        if row is None or row[0] != today.isoformat():
            return None
        return [(code, date.fromisoformat(expiry), strike) for code, expiry, strike in json.loads(row[1])]

    def save_chain(self, ticker: str, first: date, last: date, today: date, contracts: list[tuple[str, date, float]]) -> None:
        payload = json.dumps([(code, expiry.isoformat(), strike) for code, expiry, strike in contracts])
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM chain_cache WHERE fetched_on <> ?", (today.isoformat(),))
            conn.execute(
                "INSERT OR REPLACE INTO chain_cache (ticker, first_expiry, last_expiry, fetched_on, payload) VALUES (?, ?, ?, ?, ?)",
                (ticker, first.isoformat(), last.isoformat(), today.isoformat(), payload),
            )

    # ---- scan history ----------------------------------------------------

    def save_scan(self, result: ScanResult) -> int:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO scans (generated_at, ranking_metric, universe_size, dipping, candidates, positions_status, warnings)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result.generated_at.isoformat(),
                    result.ranking_metric,
                    len(result.universe),
                    len(result.dipping),
                    len(result.candidates),
                    result.positions_status,
                    json.dumps(result.warnings),
                ),
            )
            scan_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO scan_dips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        scan_id, item.ticker, item.spot, item.day_change_pct, item.off_high_pct, item.n_day_return_pct,
                        item.rsi, int(item.is_dip),
                        *_vol_fields(result, item.ticker),
                    )
                    for item in result.dips
                ],
            )
            conn.executemany(
                "INSERT INTO scan_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        scan_id, rank, item.ticker, item.code, item.expiry.isoformat(), item.dte, item.strike, item.spot,
                        item.bid, item.ask, item.premium, item.delta, int(item.delta_estimated), item.iv, item.iv_rank,
                        item.iv_hv_ratio, item.open_interest, item.otm_pct, item.annualized_yield, item.score,
                    )
                    for rank, item in enumerate(result.candidates, 1)
                ],
            )
        return scan_id

    def recent_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT s.id, s.generated_at, s.ranking_metric, s.universe_size, s.dipping, s.candidates,
                       c.ticker AS top_ticker, c.strike AS top_strike, c.expiry AS top_expiry,
                       c.premium AS top_premium, c.annualized_yield AS top_yield
                FROM scans s LEFT JOIN scan_candidates c ON c.scan_id = s.id AND c.rank = 1
                ORDER BY s.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class CachedMoomooClient:
    """Wraps a MoomooClient; daily bars and chain listings are served from the day cache."""

    def __init__(
        self, client: Any, store: OptionsStore, today: Optional[Callable[[], date]] = None, refresh: bool = False
    ) -> None:
        self._client = client
        self._store = store
        self._refresh = refresh  # True: ignore cached data but still save fresh data
        self._today = today or (lambda: datetime.now(ET).date())
        self.hits = 0
        self.misses = 0

    def daily_bars(self, ticker: str, start: date) -> list[DailyBar]:
        today = self._today()
        cached = None if self._refresh else self._store.cached_bars(ticker, start, today)
        if cached is not None:
            self.hits += 1
            return [bar for bar in cached if bar.date >= start]
        self.misses += 1
        bars = self._client.daily_bars(ticker, start)
        self._store.save_bars(ticker, start, today, bars)
        return bars

    def put_chain_codes(self, ticker: str, first_expiry: date, last_expiry: date) -> list[tuple[str, date, float]]:
        today = self._today()
        cached = None if self._refresh else self._store.cached_chain(ticker, first_expiry, last_expiry, today)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        contracts = self._client.put_chain_codes(ticker, first_expiry, last_expiry)
        self._store.save_chain(ticker, first_expiry, last_expiry, today, contracts)
        return contracts

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _vol_fields(result: ScanResult, ticker: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    vol = result.vols.get(ticker)
    if vol is None:
        return None, None, None
    return vol.iv, vol.iv_rank, vol.iv_hv_ratio
