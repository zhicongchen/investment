from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from options.config import ManagementConfig
from options.positions import ShortPut, advise, short_puts_from_positions

TODAY = date(2026, 9, 24)


def ib_position(sec_type="OPT", right="P", qty=-2, avg_cost=250.0, strike=90.0, expiry="20261016"):
    contract = SimpleNamespace(
        secType=sec_type, right=right, symbol="abc", lastTradeDateOrContractMonth=expiry, strike=strike, multiplier="100", currency="USD"
    )
    return SimpleNamespace(account="U1", contract=contract, position=qty, avgCost=avg_cost)


def test_short_puts_parsed_from_ibkr_positions():
    positions = [ib_position(), ib_position(sec_type="STK"), ib_position(right="C"), ib_position(qty=1)]
    puts = short_puts_from_positions(positions)
    assert puts == [ShortPut(account="U1", ticker="ABC", expiry=date(2026, 10, 16), strike=90.0, contracts=2, entry_credit=2.5)]


def put(expiry=date(2026, 10, 16), strike=90.0, credit=2.5):
    return ShortPut(account="U1", ticker="ABC", expiry=expiry, strike=strike, contracts=2, entry_credit=credit)


def test_take_profit():
    advice = advise(put(), TODAY, ManagementConfig(), spot=100, option_mid=1.0, delta=-0.1)
    assert advice.action == "TAKE PROFIT"
    assert abs(advice.profit_pct - 0.6) < 1e-9
    assert advice.unrealized_pnl == 300.0


def test_tested_position_should_be_defended():
    advice = advise(put(), TODAY, ManagementConfig(), spot=88, option_mid=4.0, delta=-0.6)
    assert advice.action == "ROLL / DEFEND"


def test_near_expiry():
    advice = advise(put(expiry=date(2026, 9, 29)), TODAY, ManagementConfig(), spot=95, option_mid=2.0, delta=-0.2)
    assert advice.action == "CLOSE OR ROLL"


def test_hold_without_quote():
    advice = advise(put(), TODAY, ManagementConfig(), spot=100)
    assert advice.action == "HOLD"
    assert "no live option quote" in advice.reasons
