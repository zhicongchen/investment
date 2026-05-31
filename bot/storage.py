from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from bot.types import AccountSnapshot, Fill, OrderIntent, Position, SubmitResult


class BotStorage:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def close(self) -> None:
        self.conn.close()

    def init_db(self) -> None:
        self.conn.executescript(
            """
            create table if not exists bot_runs (
                id integer primary key autoincrement,
                mode text not null,
                status text not null,
                message text not null default '',
                created_at text not null
            );
            create table if not exists account_snapshots (
                id integer primary key autoincrement,
                account text not null,
                net_liquidation real,
                buying_power real,
                currency text not null,
                created_at text not null
            );
            create table if not exists positions (
                id integer primary key autoincrement,
                account text not null,
                ticker text not null,
                quantity real not null,
                average_cost real,
                market_price real,
                market_value real,
                sec_type text not null,
                exchange text not null,
                currency text not null,
                updated_at text not null,
                unique(account, ticker)
            );
            create table if not exists decisions (
                id integer primary key autoincrement,
                ticker text not null,
                side text not null,
                quantity real not null,
                order_type text not null,
                limit_price real not null,
                time_in_force text not null,
                estimated_notional real not null,
                reason text not null,
                created_at text not null
            );
            create table if not exists risk_checks (
                id integer primary key autoincrement,
                decision_id integer,
                allowed integer not null,
                reasons text not null,
                created_at text not null
            );
            create table if not exists submitted_orders (
                id integer primary key autoincrement,
                decision_id integer not null,
                ticker text not null,
                side text not null,
                quantity real not null,
                limit_price real not null,
                ibkr_order_id integer,
                perm_id integer,
                status text not null,
                filled real not null,
                remaining real not null,
                average_fill_price real,
                raw_message text not null,
                created_at text not null,
                updated_at text not null
            );
            create table if not exists fills (
                id integer primary key autoincrement,
                execution_id text not null unique,
                ibkr_order_id integer,
                ticker text not null,
                side text not null,
                shares real not null,
                price real not null,
                time text,
                created_at text not null
            );
            """
        )
        self.conn.commit()

    def record_run(self, mode: str, status: str, message: str = "") -> int:
        now = _now()
        cursor = self.conn.execute(
            "insert into bot_runs(mode, status, message, created_at) values (?, ?, ?, ?)",
            (mode, status, message, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def record_account(self, account: AccountSnapshot) -> None:
        self.conn.execute(
            """
            insert into account_snapshots(account, net_liquidation, buying_power, currency, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (account.account, account.net_liquidation, account.buying_power, account.currency, _now()),
        )
        self.conn.commit()

    def upsert_positions(self, positions: Iterable[Position]) -> None:
        now = _now()
        for position in positions:
            self.conn.execute(
                """
                insert into positions(account, ticker, quantity, average_cost, market_price, market_value, sec_type, exchange, currency, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(account, ticker) do update set
                    quantity=excluded.quantity,
                    average_cost=excluded.average_cost,
                    market_price=excluded.market_price,
                    market_value=excluded.market_value,
                    sec_type=excluded.sec_type,
                    exchange=excluded.exchange,
                    currency=excluded.currency,
                    updated_at=excluded.updated_at
                """,
                (
                    position.account,
                    position.ticker,
                    position.quantity,
                    position.average_cost,
                    position.market_price,
                    position.market_value,
                    position.sec_type,
                    position.exchange,
                    position.currency,
                    now,
                ),
            )
        self.conn.commit()

    def record_decision(self, intent: OrderIntent) -> int:
        cursor = self.conn.execute(
            """
            insert into decisions(ticker, side, quantity, order_type, limit_price, time_in_force, estimated_notional, reason, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.ticker,
                intent.side,
                intent.quantity,
                intent.order_type,
                intent.limit_price,
                intent.time_in_force,
                intent.estimated_notional,
                intent.reason,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def record_risk_check(self, decision_id: Optional[int], allowed: bool, reasons: list[str]) -> None:
        self.conn.execute(
            "insert into risk_checks(decision_id, allowed, reasons, created_at) values (?, ?, ?, ?)",
            (decision_id, int(allowed), json.dumps(reasons), _now()),
        )
        self.conn.commit()

    def record_submission(self, decision_id: int, intent: OrderIntent, result: SubmitResult) -> None:
        now = _now()
        self.conn.execute(
            """
            insert into submitted_orders(
                decision_id, ticker, side, quantity, limit_price, ibkr_order_id, perm_id,
                status, filled, remaining, average_fill_price, raw_message, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                intent.ticker,
                intent.side,
                intent.quantity,
                intent.limit_price,
                result.order_id,
                result.perm_id,
                result.status,
                result.filled,
                result.remaining,
                result.average_fill_price,
                result.raw_message,
                now,
                now,
            ),
        )
        self.conn.commit()

    def record_fills(self, fills: Iterable[Fill]) -> None:
        for fill in fills:
            self.conn.execute(
                """
                insert or ignore into fills(execution_id, ibkr_order_id, ticker, side, shares, price, time, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.execution_id,
                    fill.order_id,
                    fill.ticker,
                    fill.side,
                    fill.shares,
                    fill.price,
                    fill.time.isoformat() if fill.time else None,
                    _now(),
                ),
            )
        self.conn.commit()

    def daily_submitted_notional(self, day_prefix: str) -> float:
        value = self.conn.execute(
            """
            select coalesce(sum(quantity * limit_price), 0) as total
            from submitted_orders
            where created_at like ?
            """,
            (f"{day_prefix}%",),
        ).fetchone()["total"]
        return float(value or 0)

    def recent_submission_exists(self, ticker: str, side: str, since_iso: str) -> bool:
        row = self.conn.execute(
            """
            select 1
            from submitted_orders
            where ticker = ? and side = ? and created_at >= ?
            limit 1
            """,
            (ticker, side, since_iso),
        ).fetchone()
        return row is not None

    def count(self, table: str) -> int:
        if table not in {"bot_runs", "decisions", "risk_checks", "submitted_orders", "fills", "positions"}:
            raise ValueError("Unsupported table")
        return int(self.conn.execute(f"select count(*) as count from {table}").fetchone()["count"])


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
