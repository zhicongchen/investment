from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from options.cli import is_market_hours
from options.config import OptionsConfig, load_config
from options.positions import ShortPut
from options.report import to_console, write_reports
from options.scanner import ET, merge_live_price, run_scan
from shared.moomoo import DailyBar, OptionQuote, UnderlyingVol

NOW = datetime(2026, 9, 24, 11, 0, tzinfo=ET)
TODAY = NOW.date()


class FakeMoomoo:
    """Two tickers: DOWN has fallen 15%, FLAT has not."""

    def __init__(self):
        self.chain_calls: list[tuple[str, date, date]] = []

    def watchlist_tickers(self, groups):
        return {"Tech": ["DOWN", "FLAT"]}

    def stock_snapshots(self, tickers):
        prices = {"DOWN": 85.0, "FLAT": 100.0, "HELD": 50.0}
        return {t: {"last_price": prices[t], "prev_close": prices[t], "update_time": f"{TODAY} 11:00:00"} for t in tickers}

    def daily_bars(self, ticker, start):
        first = TODAY - timedelta(days=40)
        return [DailyBar(date=first + timedelta(days=index), high=100.0, close=100.0) for index in range(40)]

    def underlying_vol(self, tickers):
        return {t: UnderlyingVol(iv=0.55, iv_rank=72, iv_percentile=80, hv_30d=0.35) for t in tickers}

    def put_chain_codes(self, ticker, first, last):
        self.chain_calls.append((ticker, first, last))
        expiry = TODAY + timedelta(days=21)
        if ticker == "HELD":
            return [("US.HELD45P", date(2026, 10, 16), 45.0)]
        return [(f"US.{ticker}{strike}P", expiry, float(strike)) for strike in (50, 75, 78, 80, 90)]

    def option_quotes(self, ticker, contracts):
        table = {75.0: (0.95, 1.0, -0.16), 78.0: (1.5, 1.6, -0.24), 80.0: (2.0, 2.1, -0.30), 45.0: (0.4, 0.5, -0.12)}
        quotes = []
        for code, expiry, strike in contracts:
            bid, ask, delta = table[strike]
            quotes.append(OptionQuote(code=code, ticker=ticker, expiry=expiry, strike=strike, bid=bid, ask=ask, delta=delta, iv=0.5, open_interest=900))
        return quotes


def held_puts(_config):
    return [ShortPut(account="U1", ticker="HELD", expiry=date(2026, 10, 16), strike=45.0, contracts=1, entry_credit=1.5)]


def test_scan_end_to_end(tmp_path):
    config = OptionsConfig()
    client = FakeMoomoo()
    result = run_scan(config, client, now=NOW, fetch_positions=held_puts)

    assert result.universe == ["DOWN", "FLAT"]
    assert [item.ticker for item in result.dipping] == ["DOWN"]
    # Strikes above spot (90) and deeper than max_otm (50) never reach the quote request.
    assert [item.strike for item in result.candidates] == [80.0, 78.0, 75.0]
    assert result.candidates[0].iv_rank == 72
    assert ("DOWN", TODAY + timedelta(days=7), TODAY + timedelta(days=45)) in client.chain_calls

    assert result.positions_status.startswith("1 short put")
    assert result.positions[0].action == "TAKE PROFIT"
    assert result.exposure == {"HELD": {"contracts": 1, "collateral": 4500.0}}

    paths = write_reports(result, tmp_path)
    payload = json.loads(paths["latest_json"].read_text(encoding="utf-8"))
    assert payload["candidates"][0]["ticker"] == "DOWN"
    assert payload["positions"][0]["action"] == "TAKE PROFIT"
    markdown = paths["latest_markdown"].read_text(encoding="utf-8")
    assert "Top puts to sell" in markdown and "DOWN" in markdown
    assert "DOWN" in to_console(result)


def test_ibkr_failure_does_not_break_scan():
    def broken(_config):
        raise RuntimeError("TWS not reachable")

    result = run_scan(OptionsConfig(), FakeMoomoo(), now=NOW, fetch_positions=broken)
    assert result.candidates
    assert result.positions_status == "unavailable: TWS not reachable"


def test_merge_live_price():
    bars = [DailyBar(date=date(2026, 9, 23), high=101, close=100)]
    assert merge_live_price(bars, 95.0, "2026-09-23 15:00:00")[-1] == DailyBar(date=date(2026, 9, 23), high=101, close=95.0)
    appended = merge_live_price(bars, 95.0, "2026-09-24 10:00:00")
    assert len(appended) == 2 and appended[-1].close == 95.0
    assert merge_live_price(bars, None, None) == bars


def test_market_hours():
    assert is_market_hours(datetime(2026, 9, 24, 10, 0, tzinfo=ET))
    assert not is_market_hours(datetime(2026, 9, 24, 8, 0, tzinfo=ET))
    assert not is_market_hours(datetime(2026, 9, 26, 11, 0, tzinfo=ET))  # Saturday


def test_repo_config_loads():
    config = load_config()
    assert config.puts.min_dte == 7 and config.puts.max_dte == 45
    assert config.loop.interval_minutes == 30
