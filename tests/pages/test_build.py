from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from pages.build import ET, build, load_watchlist, main

NOW = datetime(2026, 9, 24, 16, 40, tzinfo=ET)
TODAY = NOW.date()


def history(closes: list[float]) -> pd.DataFrame:
    days = pd.bdate_range(end=pd.Timestamp(TODAY), periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Adj Close": [value * 0.98 for value in closes],
            "Volume": [1_000_000] * len(closes),
        },
        index=days,
    )


class FakeTicker:
    """DOWN fell 15% over the last week; FLAT did not; BROKEN has no data."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        flat = [100.0] * 300
        down = [100.0] * 295 + [95.0, 92.0, 90.0, 88.0, 85.0]
        self._history = {"DOWN": history(down), "FLAT": history(flat), "SPY": history(flat)}.get(symbol)
        self.info = {"shortName": f"{symbol} Corp", "sector": "Tech", "exchange": "NMS", "trailingPE": 20.0, "debtToEquity": 50.0}

    def history(self, **kwargs):
        if self._history is None:
            raise ValueError("no data")
        return self._history


def test_build_writes_market_and_prices(tmp_path):
    market = build(tmp_path, ["DOWN", "FLAT", "BROKEN"], FakeTicker, NOW, pause_seconds=0)

    assert [item["ticker"] for item in market["tickers"]] == ["DOWN", "FLAT"]
    assert market["tickers"][0]["name"] == "DOWN Corp"
    assert market["fundamentals"]["DOWN"]["pe"] == 20.0
    assert "BROKEN" in market["errors"]
    assert json.loads((tmp_path / "market.json").read_text()) == market

    rows = json.loads((tmp_path / "prices" / "DOWN.json").read_text())
    assert rows[-1] == [TODAY.isoformat(), 85.0, 85.85, 84.15, 85.0, 1000000.0, 83.3]
    assert (tmp_path / "prices" / "SPY.json").exists()
    assert not (tmp_path / "prices" / "BROKEN.json").exists()


def test_load_watchlist_normalizes_and_dedupes(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers:\n  - aapl\n  - MSFT\n  - AAPL\n  - ''\n", encoding="utf-8")
    assert load_watchlist(path) == ["AAPL", "MSFT"]


def test_print_tickers_lists_the_watchlist(tmp_path, capsys):
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers: [msft, goog]\n", encoding="utf-8")
    assert main(["--watchlist", str(path), "--print-tickers"]) == 0
    assert capsys.readouterr().out.strip() == "MSFT GOOG"
