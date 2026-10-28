from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from options.config import OptionsConfig
from options.report import write_reports
from options.scanner import run_scan
from options.store import CachedMoomooClient, OptionsStore
from shared.moomoo import DailyBar
from test_scanner import NOW, TODAY, FakeMoomoo, held_puts


class CountingMoomoo(FakeMoomoo):
    def __init__(self):
        super().__init__()
        self.bar_calls = 0

    def daily_bars(self, ticker, start):
        self.bar_calls += 1
        return super().daily_bars(ticker, start)


def test_cache_reuses_bars_and_chains_within_a_day(tmp_path):
    store = OptionsStore(tmp_path / "options.db")
    raw = CountingMoomoo()
    cached = CachedMoomooClient(raw, store, today=lambda: TODAY)

    run_scan(OptionsConfig(), cached, now=NOW)
    first_bars, first_chains = raw.bar_calls, len(raw.chain_calls)
    second = run_scan(OptionsConfig(), cached, now=NOW)

    assert (first_bars, first_chains) == (2, 1)
    assert raw.bar_calls == first_bars and len(raw.chain_calls) == first_chains
    assert cached.hits == 3 and cached.misses == 3
    assert [item.strike for item in second.candidates] == [80.0, 78.0, 75.0]


def test_cache_expires_next_day_and_refresh_bypasses(tmp_path):
    store = OptionsStore(tmp_path / "options.db")
    raw = CountingMoomoo()
    start = TODAY - timedelta(days=60)
    CachedMoomooClient(raw, store, today=lambda: TODAY).daily_bars("DOWN", start)
    CachedMoomooClient(raw, store, today=lambda: TODAY + timedelta(days=1)).daily_bars("DOWN", start)
    CachedMoomooClient(raw, store, today=lambda: TODAY + timedelta(days=1), refresh=True).daily_bars("DOWN", start)
    assert raw.bar_calls == 3


def test_cache_misses_when_more_history_is_needed(tmp_path):
    store = OptionsStore(tmp_path / "options.db")
    store.save_bars("ABC", date(2026, 9, 1), TODAY, [DailyBar(date=date(2026, 9, 1), high=1, close=1)])
    assert store.cached_bars("ABC", date(2026, 9, 1), TODAY) is not None
    assert store.cached_bars("ABC", date(2026, 8, 1), TODAY) is None


def test_scan_history_is_queryable(tmp_path):
    store = OptionsStore(tmp_path / "options.db")
    result = run_scan(OptionsConfig(), FakeMoomoo(), now=NOW, fetch_positions=held_puts)
    scan_id = store.save_scan(result)

    rows = store.recent_scans()
    assert rows[0]["id"] == scan_id
    assert (rows[0]["dipping"], rows[0]["candidates"], rows[0]["top_ticker"], rows[0]["top_strike"]) == (1, 3, "DOWN", 80.0)
    with sqlite3.connect(tmp_path / "options.db") as conn:
        dips = conn.execute("SELECT ticker, is_dip, iv_rank FROM scan_dips ORDER BY ticker").fetchall()
    assert dips == [("DOWN", 1, 72.0), ("FLAT", 0, None)]


def test_reports_archive_only_when_signals_change(tmp_path):
    result = run_scan(OptionsConfig(), FakeMoomoo(), now=NOW)
    first = write_reports(result, tmp_path)
    result.generated_at = NOW + timedelta(minutes=30)
    second = write_reports(result, tmp_path)
    result.generated_at = NOW + timedelta(minutes=60)
    result.candidates = result.candidates[:1]
    third = write_reports(result, tmp_path)

    assert "markdown" in first and "markdown" not in second and "markdown" in third
    assert len(list(tmp_path.glob("scan_*.md"))) == 2
    assert (tmp_path / "latest.md").exists()
