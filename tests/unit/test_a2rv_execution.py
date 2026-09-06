from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_physics_v3.a2rv_execution import (
    Trade,
    _all_leg_weights,
    _gross_bps,
    _leg_bps,
    _leg_capacity_usd,
    _one_way_fee_bps,
    basket_weights,
    build_trades,
    summarize_trades,
)


def _row(**kwargs) -> pd.Series:
    return pd.Series(kwargs)


def test_leg_bps_long_buys_ask_sells_bid():
    entry = _row(binance__price_best_ask=100.0, binance__price_best_bid=99.9)
    exit_ = _row(binance__price_best_ask=101.0, binance__price_best_bid=100.9)
    leg = _leg_bps(entry, exit_, "binance", weight=0.5)
    expected = 0.5 * 1e4 * np.log(100.9 / 100.0)
    assert leg == pytest.approx(expected)


def test_leg_bps_short_sells_bid_buys_ask():
    entry = _row(binance__price_best_ask=100.1, binance__price_best_bid=100.0)
    exit_ = _row(binance__price_best_ask=99.0, binance__price_best_bid=98.9)
    leg = _leg_bps(entry, exit_, "binance", weight=-0.5)
    # short: sell at entry bid (100.0), buy back at exit ask (99.0) -> positive, scaled by |weight|
    expected = -0.5 * 1e4 * np.log(99.0 / 100.0)
    assert leg == pytest.approx(expected)


def test_basket_weights_normalizes_and_excludes_trigger_venue():
    row = _row(
        binance__price_weight=2.0, binance__price_mid=100.0,
        bybit__price_weight=1.0, bybit__price_mid=100.0,
        okx__price_weight=1.0, okx__price_mid=100.0,
    )
    weights = basket_weights(row, venue="binance", venues=("binance", "bybit", "okx"))
    assert "binance" not in weights
    assert weights == pytest.approx({"bybit": 0.5, "okx": 0.5})


def test_all_leg_weights_is_a_dollar_neutral_50_50_split():
    basket = {"bybit": 0.5, "okx": 0.5}
    weights = _all_leg_weights("binance", trigger_direction=1, basket=basket)
    assert weights["binance"] == pytest.approx(0.5)
    assert weights["bybit"] == pytest.approx(-0.25)
    assert weights["okx"] == pytest.approx(-0.25)
    assert sum(abs(w) for w in weights.values()) == pytest.approx(1.0)


def test_all_leg_weights_flips_sign_for_rich_trigger():
    basket = {"bybit": 1.0}
    weights = _all_leg_weights("binance", trigger_direction=-1, basket=basket)
    assert weights["binance"] == pytest.approx(-0.5)
    assert weights["bybit"] == pytest.approx(0.5)


def test_one_way_fee_bps_is_weighted_average_over_all_legs():
    weights = {"binance": 0.5, "bybit": -0.5}
    fee = _one_way_fee_bps(weights)
    assert fee == pytest.approx(0.5 * 5.0 + 0.5 * 5.5)


def test_gross_bps_sums_every_leg_and_nans_if_any_leg_is_missing():
    entry = _row(binance__price_best_ask=100.0, binance__price_best_bid=99.9, bybit__price_best_ask=100.0, bybit__price_best_bid=99.9)
    exit_ = _row(binance__price_best_ask=101.0, binance__price_best_bid=100.9, bybit__price_best_ask=np.nan, bybit__price_best_bid=np.nan)
    weights = {"binance": 0.5, "bybit": -0.5}
    assert np.isnan(_gross_bps(entry, exit_, weights))


def test_leg_capacity_is_the_binding_leg_across_mixed_signs():
    row = _row(
        binance__ask_depth_5bps=1.0, binance__price_best_ask=100.0,  # long leg: $100 depth, |w|=0.5 -> cap $200
        bybit__bid_depth_5bps=1000.0, bybit__price_best_bid=100.0,   # short leg: $100,000 depth, |w|=0.5 -> cap $200,000
    )
    weights = {"binance": 0.5, "bybit": -0.5}
    capacity = _leg_capacity_usd(row, weights)
    assert capacity == pytest.approx(200.0)


def test_trade_net_bps_charges_roundtrip_fee_twice():
    trade = Trade(
        symbol="BTCUSDT", trigger_venue="binance", entry_idx=0, exit_idx=20, trigger_direction=1,
        entry_asof_ns=0, weights={"binance": 0.5, "bybit": -0.5}, gross_bps=10.0, one_way_fee_bps=5.0,
        capacity_usd=100_000.0, delayed_gross_bps={0: 10.0}, delayed_one_way_fee_bps={0: 5.0},
    )
    assert trade.net_bps == pytest.approx(10.0 - 2 * 5.0)
    assert trade.delayed_net_bps(0) == pytest.approx(10.0 - 2 * 5.0)
    assert np.isnan(trade.delayed_net_bps(999))  # latency not in the grid for this trade


def test_build_trades_end_to_end_on_synthetic_dislocation():
    from market_physics_v3.a2rv_execution import FrozenThresholds

    n = 500
    asof = 1_000_000_000 + np.arange(n, dtype=np.int64) * 100_000_000
    mid = 100 + np.cumsum(np.zeros(n))  # flat mid, isolates the fee/threshold mechanics
    frame = pd.DataFrame({"asof_ns": asof, "symbol": "BTCUSDT"})
    for v in ("binance", "bybit", "okx"):
        frame[f"{v}__price_mid"] = mid
        frame[f"{v}__price_best_bid"] = mid - 0.01
        frame[f"{v}__price_best_ask"] = mid + 0.01
        frame[f"{v}__price_weight"] = 1.0
        frame[f"{v}__ask_depth_5bps"] = 1000.0
        frame[f"{v}__bid_depth_5bps"] = 1000.0
        frame[f"{v}__price_dislocation_bps"] = 0.0
    # force one clean trigger: binance cheap at row 10
    frame.loc[10, "binance__price_dislocation_bps"] = -50.0

    thresholds = {v: FrozenThresholds(lo=-10.0, hi=10.0) for v in ("binance", "bybit", "okx")}
    trades = build_trades(frame, "BTCUSDT", thresholds, venues=("binance", "bybit", "okx"), cadence_ms=100)
    assert len(trades) == 1
    assert trades[0].trigger_venue == "binance"
    assert trades[0].trigger_direction == 1  # dislocation <= lo -> cheap -> long

    summary = summarize_trades(trades)
    assert summary["n_trades"] == 1
    assert set(summary["latency_sensitivity_net_bps"].keys()) == {"0", "50", "100", "250", "500"}
