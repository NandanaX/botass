from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.risk.calculator import RiskInput, calculate_risk


def test_long_sizing_without_fees():
    out = calculate_risk(RiskInput(account_balance=10_000, risk_pct=1, direction="LONG", entry_price=100,
                                   stop_loss=95, take_profit=110, fee_rate_pct=0, include_fees_in_sizing=False))
    assert out.max_loss == 100
    assert out.quantity == pytest.approx(20)
    assert out.notional == pytest.approx(2000)
    assert out.risk_reward == pytest.approx(2)
    assert out.potential_gross_profit == pytest.approx(200)
    assert out.potential_net_profit == pytest.approx(200)
    assert out.implied_leverage == pytest.approx(0.2)


def test_short_sizing_with_fees_keeps_loss_at_budget():
    inp = RiskInput(account_balance=5_000, risk_pct=2, direction="SHORT", entry_price=200, stop_loss=210,
                    take_profit=180, fee_rate_pct=0.05)
    out = calculate_risk(inp)
    fee = 0.0005
    expected_qty = 100 / (10 + fee * (200 + 210))
    assert out.quantity == pytest.approx(expected_qty)
    assert out.estimated_loss_at_stop_net == pytest.approx(100)
    assert out.potential_gross_profit == pytest.approx(expected_qty * 20)
    assert out.estimated_fees_at_target == pytest.approx(fee * expected_qty * (200 + 180))
    assert out.potential_net_profit == pytest.approx(out.potential_gross_profit - out.estimated_fees_at_target)


@pytest.mark.parametrize("kw", [
    {"direction": "LONG", "entry_price": 100, "stop_loss": 101},
    {"direction": "SHORT", "entry_price": 100, "stop_loss": 99},
    {"direction": "LONG", "entry_price": 100, "stop_loss": 95, "take_profit": 99},
    {"direction": "SHORT", "entry_price": 100, "stop_loss": 105, "take_profit": 101},
])
def test_invalid_levels_rejected(kw):
    with pytest.raises(ValidationError):
        RiskInput(account_balance=1000, **kw)
