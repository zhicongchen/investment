from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd

from options.scanner import ET
from options.yahoo import YahooClient, historical_vol
from pages.build import build, load_watchlist

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


class FakeChain:
    def __init__(self, puts: pd.DataFrame) -> None:
        self.puts = puts


class FakeTicker:
    """DOWN fell 15% over the last week; FLAT did not; BROKEN has no data."""

    expiry = (TODAY + timedelta(days=21)).isoformat()

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        flat = [100.0] * 300
        down = [100.0] * 295 + [95.0, 92.0, 90.0, 88.0, 85.0]
        self._history = {"DOWN": history(down), "FLAT": history(flat), "SPY": history(flat)}.get(symbol)
        self.info = {"shortName": f"{symbol} Corp", "sector": "Tech", "exchange": "NMS", "trailingPE": 20.0, "debtToEquity": 50.0}
        self.options = (self.expiry,) if symbol == "DOWN" else ()

    def history(self, **kwargs):
        if self._history is None:
            raise ValueError("no data")
        return self._history

    def option_chain(self, expiry: str) -> FakeChain:
        rows = []
        for strike in range(60, 90):
            otm = 1 - strike / 85.0
            bid = round(max(0.05, 85 * 0.035 * (1 - otm * 4)), 2)
            rows.append(
                {
                    "contractSymbol": f"DOWN{expiry}P{strike}",
                    "strike": float(strike),
                    "bid": 0.0 if strike == 80 else bid,
                    "ask": 0.0 if strike == 80 else round(bid * 1.05, 2),
                    "lastPrice": bid,
                    "lastTradeDate": pd.Timestamp(TODAY),
                    "openInterest": 500,
                    "volume": 20,
                    "impliedVolatility": 0.00001 if strike == 61 else 0.45,
                }
            )
        return FakeChain(pd.DataFrame(rows))


def test_build_writes_market_prices_and_scan(tmp_path):
    market = build(tmp_path, ["DOWN", "FLAT", "BROKEN"], FakeTicker, NOW, pause_seconds=0)

    assert [item["ticker"] for item in market["tickers"]] == ["DOWN", "FLAT"]
    assert market["tickers"][0]["name"] == "DOWN Corp"
    assert market["fundamentals"]["DOWN"]["pe"] == 20.0
    assert "BROKEN" in market["errors"]
    assert json.loads((tmp_path / "market.json").read_text()) == market

    rows = json.loads((tmp_path / "prices" / "DOWN.json").read_text())
    assert rows[-1] == [TODAY.isoformat(), 85.0, 85.85, 84.15, 85.0, 1000000.0, 83.3]
    assert (tmp_path / "prices" / "SPY.json").exists()

    scan = json.loads((tmp_path / "options-scan.json").read_text())
    assert scan["universe"] == ["BROKEN", "DOWN", "FLAT"]
    assert [row["ticker"] for row in scan["dips"] if row["is_dip"]] == ["DOWN"]
    assert scan["candidates"], scan["rejections"]
    assert all(item["ticker"] == "DOWN" and item["delta_estimated"] for item in scan["candidates"])
    assert scan["vols"]["DOWN"]["iv"] == 0.45
    assert "BROKEN" in scan["errors"]
    assert any("Black-Scholes" in warning for warning in scan["warnings"])
    assert any("last trade price" in warning for warning in scan["warnings"])
    assert not any("Moomoo" in warning for warning in scan["warnings"])
    assert scan["positions_status"].startswith("IBKR positions are not available")


def test_yahoo_quotes_drop_placeholder_iv_and_fill_missing_bid_ask():
    client = YahooClient(today=TODAY, ticker_factory=FakeTicker, pause_seconds=0)
    expiry = date.fromisoformat(FakeTicker.expiry)
    codes = client.put_chain_codes("DOWN", TODAY, TODAY + timedelta(days=30))
    quotes = {quote.strike: quote for quote in client.option_quotes("DOWN", [code for code in codes if code[2] in (61.0, 80.0, 75.0)])}

    assert all(code[1] == expiry for code in codes)
    assert quotes[61.0].iv is None
    assert quotes[75.0].iv == 0.45 and quotes[75.0].bid < quotes[75.0].ask
    assert quotes[80.0].bid == quotes[80.0].ask == quotes[80.0].last
    assert client.last_price_fallbacks == 1


def test_historical_vol_needs_a_full_window():
    assert historical_vol([100.0] * 30, 30) is None
    assert historical_vol([100.0] * 31, 30) == 0.0


def test_load_watchlist_normalizes_and_dedupes(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers:\n  - aapl\n  - MSFT\n  - AAPL\n  - ''\n", encoding="utf-8")
    assert load_watchlist(path) == ["AAPL", "MSFT"]
