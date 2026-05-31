from __future__ import annotations

from datetime import datetime

from bot.config import BotConfig
from bot.runner import run_loop, run_once, strategy_from_config
from bot.storage import BotStorage
from bot.strategy import ConservativeDipBuyStrategy, ExampleHookStrategy, NoOpStrategy
from bot.types import AccountSnapshot, BrokerStatus, ContractSummary, DailyBar, Fill, MarketSnapshot, Position, SubmitResult


def config(tmp_path, **kwargs) -> BotConfig:
    defaults = {
        "db_path": tmp_path / "bot.db",
        "live_trading_enabled": True,
        "tickers": ("AAPL",),
        "loop_interval_seconds": 0,
        "max_order_notional_usd": 500,
        "max_daily_notional_usd": 2000,
        "allow_after_hours": True,
        "strategy": "noop",
    }
    defaults.update(kwargs)
    return BotConfig(**defaults)


class FakeClient:
    def __init__(self, connected=True, qualify=True, price=100):
        self.connected = connected
        self.qualify = qualify
        self.price = price
        self.submitted = []

    def connect(self):
        return BrokerStatus(connected=self.connected, message="fake")

    def account_summary(self):
        return AccountSnapshot(account="DU123", net_liquidation=10000, buying_power=5000)

    def positions(self):
        return [Position(account="DU123", ticker="AAPL", quantity=10)]

    def market_snapshot(self, tickers):
        return {ticker: MarketSnapshot(ticker=ticker, last_price=self.price, timestamp=datetime(2026, 6, 1, 10, 0)) for ticker in tickers}

    def daily_history(self, tickers):
        values = [100 + index * 0.1 for index in range(180)]
        values += [118, 119, 120, 121, 122, 123, 124, 125, 126, 127]
        values += [128, 129, 130, 131, 132, 133, 134, 135, 132, 130]
        return {
            ticker: [DailyBar(date=datetime(2025, 1, 1), close=value) for value in values]
            for ticker in tickers
        }

    def qualify_stock(self, ticker):
        if not self.qualify:
            raise RuntimeError("Contract qualification failed")
        return ContractSummary(symbol=ticker)

    def place_limit_order(self, intent):
        self.submitted.append(intent)
        return SubmitResult(order_id=77, perm_id=88, status="Submitted", remaining=intent.quantity)

    def fills(self):
        return [Fill(execution_id="E1", order_id=77, ticker="AAPL", side="BUY", shares=1, price=99)]


def test_strategy_hook_returns_intents_without_ibkr():
    cfg = config(__import__("pathlib").Path("."))
    strategy = ExampleHookStrategy()
    context = type(
        "Context",
        (),
        {
            "market_data": {"AAPL": MarketSnapshot(ticker="AAPL", last_price=100)},
        },
    )()

    intents = strategy.generate(context)

    assert len(intents) == 1
    assert intents[0].ticker == "AAPL"
    assert intents[0].order_type == "LMT"


def test_once_evaluates_submits_and_persists(tmp_path):
    cfg = config(tmp_path)
    storage = BotStorage(cfg.db_path)
    client = FakeClient()

    result = run_once(config=cfg, client=client, storage=storage, strategy=ExampleHookStrategy())

    assert result == {"status": "ok", "decisions": 1, "submitted": 1, "blocked": 0}
    assert storage.count("decisions") == 1
    assert storage.count("submitted_orders") == 1
    assert storage.count("fills") == 1
    assert len(client.submitted) == 1


def test_contract_failure_blocks_and_does_not_submit(tmp_path):
    cfg = config(tmp_path)
    storage = BotStorage(cfg.db_path)
    client = FakeClient(qualify=False)

    result = run_once(config=cfg, client=client, storage=storage, strategy=ExampleHookStrategy())

    assert result["blocked"] == 1
    assert storage.count("risk_checks") == 1
    assert storage.count("submitted_orders") == 0
    assert client.submitted == []


def test_once_blocks_when_disconnected(tmp_path):
    cfg = config(tmp_path)
    storage = BotStorage(cfg.db_path)
    client = FakeClient(connected=False)

    result = run_once(config=cfg, client=client, storage=storage, strategy=ExampleHookStrategy())

    assert result["blocked"] == 1
    assert storage.count("submitted_orders") == 0


def test_loop_can_run_one_iteration(tmp_path, monkeypatch):
    calls = []

    def fake_once(config=None):
        calls.append(config)
        return {"status": "ok", "decisions": 0, "submitted": 0, "blocked": 0}

    monkeypatch.setattr("bot.runner.run_once", fake_once)

    run_loop(config=config(tmp_path), max_iterations=1)

    assert len(calls) == 1


def test_strategy_selection_defaults_to_noop(tmp_path):
    assert isinstance(strategy_from_config(config(tmp_path, strategy="noop")), NoOpStrategy)


def test_strategy_selection_can_use_conservative_dip(tmp_path):
    assert isinstance(strategy_from_config(config(tmp_path, strategy="conservative_dip")), ConservativeDipBuyStrategy)


def test_conservative_dip_dry_run_records_blocked_decision_without_submission(tmp_path):
    cfg = config(
        tmp_path,
        strategy="conservative_dip",
        live_trading_enabled=False,
        tickers=("AAPL",),
        core_etfs=("SPY", "VOO", "QQQ"),
    )
    storage = BotStorage(cfg.db_path)
    client = FakeClient()

    result = run_once(config=cfg, client=client, storage=storage)

    assert result == {"status": "ok", "decisions": 1, "submitted": 0, "blocked": 1}
    assert storage.count("decisions") == 1
    assert storage.count("risk_checks") == 1
    assert storage.count("submitted_orders") == 0
    assert client.submitted == []
