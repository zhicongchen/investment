from __future__ import annotations

from dataclasses import replace

import pandas as pd

from quant.config import DataConfig, QuantConfig
from quant.data import loader


def test_download_throttles_between_tickers(monkeypatch, tmp_path):
    calls: list[float] = []

    def fake_download(ticker, **kwargs):
        index = pd.date_range("2024-01-02 09:30", periods=2, freq="min", tz="America/New_York")
        return pd.DataFrame(
            {"Open": [1.0, 1.1], "High": [1.1, 1.2], "Low": [0.9, 1.0], "Close": [1.0, 1.1], "Volume": [100, 200]},
            index=pd.Index(index, name="Datetime"),
        )

    monkeypatch.setattr(loader.yf, "download", fake_download)
    monkeypatch.setattr(loader.time, "sleep", lambda seconds: calls.append(seconds))
    monkeypatch.setattr(loader.random, "uniform", lambda start, end: 0.25)
    config = replace(
        QuantConfig(watchlist=("AAA", "BBB"), data_dir=tmp_path / "data", reports_dir=tmp_path / "reports"),
        data=DataConfig(period="7d", interval="1m", request_delay_seconds=1.0, request_delay_jitter_seconds=0.5),
    )

    loader.download_intraday_data(config, target_ticker="AAA")

    assert calls == [1.25, 1.25, 1.25]
