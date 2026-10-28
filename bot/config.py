from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from shared.env import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]

load_dotenv(BASE_DIR / ".env", BASE_DIR / "bot" / ".env")


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _tickers(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class BotConfig:
    ibkr_host: str = field(default_factory=lambda: os.getenv("BOT_IBKR_HOST", "127.0.0.1").strip())
    ibkr_port: int = field(default_factory=lambda: int(os.getenv("BOT_IBKR_PORT", "7496")))
    ibkr_client_id: int = field(default_factory=lambda: int(os.getenv("BOT_IBKR_CLIENT_ID", "20")))
    live_trading_enabled: bool = field(default_factory=lambda: _bool("BOT_LIVE_TRADING_ENABLED", False))
    max_order_notional_usd: float = field(default_factory=lambda: float(os.getenv("BOT_MAX_ORDER_NOTIONAL_USD", "500")))
    max_daily_notional_usd: float = field(default_factory=lambda: float(os.getenv("BOT_MAX_DAILY_NOTIONAL_USD", "2000")))
    allow_after_hours: bool = field(default_factory=lambda: _bool("BOT_ALLOW_AFTER_HOURS", False))
    allow_shorts: bool = field(default_factory=lambda: _bool("BOT_ALLOW_SHORTS", False))
    loop_interval_seconds: int = field(default_factory=lambda: int(os.getenv("BOT_LOOP_INTERVAL_SECONDS", "300")))
    duplicate_cooldown_seconds: int = field(default_factory=lambda: int(os.getenv("BOT_DUPLICATE_COOLDOWN_SECONDS", "900")))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("BOT_DB_PATH", str(BASE_DIR / "data" / "ibkr_bot_audit.db"))))
    tickers: tuple[str, ...] = field(default_factory=lambda: tuple(_tickers(os.getenv("BOT_TICKERS", "AAPL,MSFT,SPY"))))
    strategy: str = field(default_factory=lambda: os.getenv("BOT_STRATEGY", "noop").strip().lower())
    dip_buy_notional_usd: float = field(default_factory=lambda: float(os.getenv("BOT_DIP_BUY_NOTIONAL_USD", "100")))
    core_etfs: tuple[str, ...] = field(default_factory=lambda: tuple(_tickers(os.getenv("BOT_CORE_ETFS", "SPY,VOO,QQQ"))))
    max_position_pct: float = field(default_factory=lambda: float(os.getenv("BOT_MAX_POSITION_PCT", "0.20")))
    trend_ma_days: int = field(default_factory=lambda: int(os.getenv("BOT_TREND_MA_DAYS", "200")))
    dip_lookback_days: int = field(default_factory=lambda: int(os.getenv("BOT_DIP_LOOKBACK_DAYS", "20")))
    dip_min_pullback_pct: float = field(default_factory=lambda: float(os.getenv("BOT_DIP_MIN_PULLBACK_PCT", "0.03")))
    limit_discount_pct: float = field(default_factory=lambda: float(os.getenv("BOT_LIMIT_DISCOUNT_PCT", "0.0025")))


def load_config() -> BotConfig:
    return BotConfig()
