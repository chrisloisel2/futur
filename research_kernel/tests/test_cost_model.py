from __future__ import annotations

import math

import pytest

from research_kernel.cost_model import (
    COST_WALL_MULTIPLE,
    CostModel,
    apply_costs,
    breakeven_capture,
    cost_sensitivity,
    cost_x2,
    estimate_round_trip_cost_bps,
    passes_cost_wall,
)


def test_round_trip_charges_fees_on_both_sides():
    c = estimate_round_trip_cost_bps(
        maker_fee_bps=2.0,
        taker_fee_bps=5.0,
        spread_bps=1.0,
        slippage_bps=0.5,
        adverse_selection_bps=2.0,
        maker_ratio=0.0,
    )
    assert c == pytest.approx(2 * 5.0 + 1.0 + 0.5 + 2.0)


def test_maker_ratio_interpolates():
    kw = dict(spread_bps=0.0, slippage_bps=0.0, adverse_selection_bps=0.0)
    all_taker = estimate_round_trip_cost_bps(2.0, 5.0, maker_ratio=0.0, **kw)
    all_maker = estimate_round_trip_cost_bps(2.0, 5.0, maker_ratio=1.0, **kw)
    half = estimate_round_trip_cost_bps(2.0, 5.0, maker_ratio=0.5, **kw)
    assert all_maker < half < all_taker


def test_maker_ratio_out_of_range_is_refused():
    with pytest.raises(ValueError):
        estimate_round_trip_cost_bps(2.0, 5.0, 0.0, 0.0, 0.0, maker_ratio=1.5)


def test_every_leg_is_charged():
    one = CostModel(taker_fee_bps=5.0, spread_bps=1.0, n_legs=1).round_trip_bps()
    two = CostModel(taker_fee_bps=5.0, spread_bps=1.0, n_legs=2).round_trip_bps()
    assert two == pytest.approx(2 * one)


def test_flat_fees_form_is_accepted():
    c = CostModel.from_spec({"fees_bps": 4.0, "spread_bps": 2.0})
    assert c.round_trip_bps() == pytest.approx(2 * 4.0 + 2.0)


def test_unknown_cost_key_is_refused():
    with pytest.raises(ValueError):
        CostModel.from_spec({"fees_bps": 4.0, "magic_discount_bps": -10.0})


def test_cost_wall_is_a_factor_of_three():
    assert COST_WALL_MULTIPLE == 3.0
    assert passes_cost_wall(30.0, 10.0)
    assert not passes_cost_wall(29.0, 10.0)


def test_breakeven_capture_above_one_means_dead():
    assert breakeven_capture(10.0, 16.0) == pytest.approx(1.6)
    assert math.isinf(breakeven_capture(0.0, 16.0))


def test_cost_sensitivity_reports_every_multiplier():
    rows = cost_sensitivity(30.0, 10.0)
    assert [r["cost_multiplier"] for r in rows] == [1.0, 1.5, 2.0, 3.0]
    assert rows[-1]["net_edge_bps"] == pytest.approx(0.0)


def test_apply_costs_and_double():
    assert apply_costs(10.0, 4.0) == 6.0
    assert cost_x2(4.0) == 8.0
