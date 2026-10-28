from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

RANKING_METRICS = ("annualized_yield", "iv_rank", "iv_hv_ratio", "blend")


@dataclass
class OpenDConfig:
    host: str = "127.0.0.1"
    port: int = 11111


@dataclass
class WatchlistConfig:
    groups: list[str] = field(default_factory=list)
    extra_tickers: list[str] = field(default_factory=list)
    exclude_tickers: list[str] = field(default_factory=list)


@dataclass
class OffHighCheck:
    enabled: bool = True
    lookback_days: int = 20
    min_pct: float = 0.10


@dataclass
class DropNDaysCheck:
    enabled: bool = True
    days: int = 5
    min_pct: float = 0.05


@dataclass
class RsiCheck:
    enabled: bool = False
    period: int = 14
    max: float = 35.0


@dataclass
class DayDropCheck:
    enabled: bool = False
    min_pct: float = 0.03


@dataclass
class DipChecks:
    off_high: OffHighCheck = field(default_factory=OffHighCheck)
    drop_n_days: DropNDaysCheck = field(default_factory=DropNDaysCheck)
    rsi: RsiCheck = field(default_factory=RsiCheck)
    day_drop: DayDropCheck = field(default_factory=DayDropCheck)


@dataclass
class TrendFilter:
    enabled: bool = False
    days: int = 200


@dataclass
class DipConfig:
    rule: str = "any"
    checks: DipChecks = field(default_factory=DipChecks)
    require_above_ma: TrendFilter = field(default_factory=TrendFilter)


@dataclass
class PutsConfig:
    min_dte: int = 7
    max_dte: int = 45
    delta_min: float = -0.35
    delta_max: float = -0.15
    min_otm_pct: float = 0.0
    max_otm_pct: float = 0.30
    min_open_interest: int = 100
    min_bid: float = 0.10
    max_spread_pct: float = 0.10
    price_basis: str = "mid"
    risk_free_rate: float = 0.04
    top_per_ticker: int = 3


@dataclass
class RankingConfig:
    metric: str = "annualized_yield"
    blend_weights: dict[str, float] = field(
        default_factory=lambda: {"annualized_yield": 0.5, "iv_rank": 0.3, "iv_hv_ratio": 0.2}
    )
    top_n: int = 20


@dataclass
class SizingConfig:
    max_collateral_per_trade: Optional[float] = None


@dataclass
class ManagementConfig:
    take_profit_pct: float = 0.50
    near_expiry_dte: int = 7
    tested_delta: float = -0.50


@dataclass
class IbkrConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 7496
    client_id: int = 31


@dataclass
class LoopConfig:
    interval_minutes: int = 30
    market_hours_only: bool = True


@dataclass
class ReportConfig:
    dir: str = "reports/options"

    @property
    def path(self) -> Path:
        path = Path(self.dir)
        return path if path.is_absolute() else BASE_DIR / path


@dataclass
class StorageConfig:
    path: str = "data/options.db"
    cache_enabled: bool = True

    @property
    def db_path(self) -> Path:
        path = Path(self.path)
        return path if path.is_absolute() else BASE_DIR / path


@dataclass
class OptionsConfig:
    opend: OpenDConfig = field(default_factory=OpenDConfig)
    watchlist: WatchlistConfig = field(default_factory=WatchlistConfig)
    dip: DipConfig = field(default_factory=DipConfig)
    puts: PutsConfig = field(default_factory=PutsConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    management: ManagementConfig = field(default_factory=ManagementConfig)
    ibkr: IbkrConfig = field(default_factory=IbkrConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def load_config(path: Optional[Path] = None) -> OptionsConfig:
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = _build(OptionsConfig, raw)
    _validate(config)
    return config


def _build(cls: type, raw: Any) -> Any:
    if not isinstance(raw, dict):
        return cls()
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in raw:
            continue
        value = raw[item.name]
        default = item.default_factory() if callable(item.default_factory) else item.default  # type: ignore[misc]
        if is_dataclass(default):
            kwargs[item.name] = _build(type(default), value)
        elif isinstance(default, list):
            kwargs[item.name] = [str(entry) for entry in (value or [])]
        else:
            kwargs[item.name] = value
    return cls(**kwargs)


def _validate(config: OptionsConfig) -> None:
    if config.dip.rule not in {"any", "all"}:
        raise ValueError(f"dip.rule must be 'any' or 'all', got {config.dip.rule!r}")
    if config.ranking.metric not in RANKING_METRICS:
        raise ValueError(f"ranking.metric must be one of {RANKING_METRICS}, got {config.ranking.metric!r}")
    if config.puts.price_basis not in {"mid", "bid"}:
        raise ValueError(f"puts.price_basis must be 'mid' or 'bid', got {config.puts.price_basis!r}")
    if config.puts.min_dte > config.puts.max_dte:
        raise ValueError("puts.min_dte must be <= puts.max_dte")
    if config.puts.delta_min > config.puts.delta_max:
        raise ValueError("puts.delta_min must be <= puts.delta_max (both negative for puts)")
    config.watchlist.extra_tickers = [ticker.strip().upper() for ticker in config.watchlist.extra_tickers if ticker.strip()]
    config.watchlist.exclude_tickers = [ticker.strip().upper() for ticker in config.watchlist.exclude_tickers if ticker.strip()]
