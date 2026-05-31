from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Optional

from bot.config import BotConfig, load_config
from bot.ibkr_client import IbkrClient
from bot.risk import evaluate_risk
from bot.storage import BotStorage
from bot.strategy import ConservativeDipBuyStrategy, NoOpStrategy, Strategy
from bot.types import ContractSummary, MarketSnapshot, StrategyContext


def run_once(
    *,
    config: Optional[BotConfig] = None,
    client: Optional[object] = None,
    storage: Optional[BotStorage] = None,
    strategy: Optional[Strategy] = None,
) -> dict[str, int | str]:
    cfg = config or load_config()
    store = storage or BotStorage(cfg.db_path)
    broker = client or IbkrClient(cfg)
    selected_strategy = strategy or strategy_from_config(cfg)
    submitted_count = 0
    blocked_count = 0
    decision_count = 0

    status = broker.connect()
    account = broker.account_summary()
    positions_list = broker.positions()
    positions = {position.ticker: position for position in positions_list}
    market_data = broker.market_snapshot(list(cfg.tickers))
    daily_history = broker.daily_history(list(cfg.tickers))
    now = datetime.now()

    if account:
        store.record_account(account)
    store.upsert_positions(positions_list)

    context = StrategyContext(
        account=account,
        positions=positions,
        market_data=market_data,
        daily_history=daily_history,
        now=now,
        config=cfg,
    )
    intents = selected_strategy.generate(context)

    for intent in intents:
        decision_count += 1
        decision_id = store.record_decision(intent)
        contract: ContractSummary | None = None
        contract_error: str | None = None
        try:
            contract = broker.qualify_stock(intent.ticker)
        except Exception as exc:
            contract_error = str(exc) or exc.__class__.__name__
        risk = evaluate_risk(
            intent=intent,
            config=cfg,
            storage=store,
            status=status,
            account=account,
            positions=positions,
            contract=contract,
            contract_error=contract_error,
            now=now,
        )
        store.record_risk_check(decision_id, risk.allowed, risk.reasons)
        if not risk.allowed:
            blocked_count += 1
            continue
        result = broker.place_limit_order(intent)
        store.record_submission(decision_id, intent, result)
        submitted_count += 1

    store.record_fills(broker.fills())
    store.record_run("once", "ok", f"decisions={decision_count} submitted={submitted_count} blocked={blocked_count}")
    return {"status": "ok", "decisions": decision_count, "submitted": submitted_count, "blocked": blocked_count}


def run_loop(config: Optional[BotConfig] = None, max_iterations: Optional[int] = None) -> None:
    cfg = config or load_config()
    iterations = 0
    while True:
        result = run_once(config=cfg)
        print(f"bot loop: {result}")
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        time.sleep(cfg.loop_interval_seconds)


def strategy_from_config(config: BotConfig) -> Strategy:
    if config.strategy == "conservative_dip":
        return ConservativeDipBuyStrategy()
    return NoOpStrategy()


def status(config: Optional[BotConfig] = None) -> dict[str, object]:
    cfg = config or load_config()
    store = BotStorage(cfg.db_path)
    client = IbkrClient(cfg)
    broker_status = client.connect()
    return {
        "connected": broker_status.connected,
        "message": broker_status.message,
        "db_path": str(cfg.db_path),
        "runs": store.count("bot_runs"),
        "decisions": store.count("decisions"),
        "submitted_orders": store.count("submitted_orders"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone guarded IBKR trading bot")
    parser.add_argument("command", choices=["once", "loop", "status"])
    parser.add_argument("--iterations", type=int, default=None)
    args = parser.parse_args()

    if args.command == "once":
        print(run_once())
    elif args.command == "loop":
        run_loop(max_iterations=args.iterations)
    else:
        print(status())


if __name__ == "__main__":
    main()
