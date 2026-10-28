"""Read open short puts from IBKR TWS (read-only) and produce management advice."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from options.config import IbkrConfig, ManagementConfig
from shared import ibkr


@dataclass(frozen=True)
class ShortPut:
    account: str
    ticker: str
    expiry: date
    strike: float
    contracts: int  # positive number of contracts sold
    entry_credit: float  # per share
    multiplier: int = 100


@dataclass
class PositionAdvice:
    position: ShortPut
    dte: int
    spot: Optional[float] = None
    option_mid: Optional[float] = None
    delta: Optional[float] = None
    profit_pct: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    action: str = "HOLD"
    reasons: list[str] = field(default_factory=list)


def fetch_short_puts(config: IbkrConfig) -> list[ShortPut]:
    """Connect read-only to TWS and return short US equity put positions."""
    with ibkr.session(config.host, config.port, config.client_id, readonly=True) as ib:
        return short_puts_from_positions(ib.reqPositions())


def short_puts_from_positions(positions: list[Any]) -> list[ShortPut]:
    results: list[ShortPut] = []
    for pos in positions:
        contract = pos.contract
        if getattr(contract, "secType", "") != "OPT" or getattr(contract, "right", "") not in {"P", "PUT"}:
            continue
        if float(pos.position) >= 0 or getattr(contract, "currency", "USD") != "USD":
            continue
        multiplier = int(float(getattr(contract, "multiplier", "") or 100))
        results.append(
            ShortPut(
                account=pos.account,
                ticker=contract.symbol.upper(),
                expiry=datetime.strptime(contract.lastTradeDateOrContractMonth[:8], "%Y%m%d").date(),
                strike=float(contract.strike),
                contracts=int(abs(float(pos.position))),
                # IBKR reports option avgCost per contract (price x multiplier).
                entry_credit=float(pos.avgCost) / multiplier,
                multiplier=multiplier,
            )
        )
    return sorted(results, key=lambda item: (item.expiry, item.ticker, item.strike))


def advise(
    position: ShortPut,
    today: date,
    config: ManagementConfig,
    spot: Optional[float] = None,
    option_mid: Optional[float] = None,
    delta: Optional[float] = None,
) -> PositionAdvice:
    advice = PositionAdvice(position=position, dte=(position.expiry - today).days, spot=spot, option_mid=option_mid, delta=delta)
    if option_mid is not None and position.entry_credit > 0:
        advice.profit_pct = (position.entry_credit - option_mid) / position.entry_credit
        advice.unrealized_pnl = (position.entry_credit - option_mid) * position.multiplier * position.contracts

    tested = []
    if spot is not None and spot < position.strike:
        tested.append(f"spot {spot:.2f} below strike {position.strike:g}")
    if delta is not None and delta <= config.tested_delta:
        tested.append(f"delta {delta:.2f} <= {config.tested_delta:.2f}")

    if advice.profit_pct is not None and advice.profit_pct >= config.take_profit_pct:
        advice.action = "TAKE PROFIT"
        advice.reasons.append(f"{advice.profit_pct:.0%} of credit captured (target {config.take_profit_pct:.0%})")
    elif tested:
        advice.action = "ROLL / DEFEND"
        advice.reasons.extend(tested)
    elif advice.dte <= config.near_expiry_dte:
        advice.action = "CLOSE OR ROLL"
        advice.reasons.append(f"{advice.dte} DTE left, gamma risk rising")
    else:
        advice.reasons.append("within plan")
    if option_mid is None:
        advice.reasons.append("no live option quote")
    return advice
