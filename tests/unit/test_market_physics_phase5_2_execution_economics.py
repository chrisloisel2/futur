from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_physics_v3.phase5_2_execution_economics import (
    Trade,
    _fee_bps,
    _gross_bps,
    _leg_capacity_usd,
    _venue_weights,
    build_trades,
    summarize_trades,
)


def _row(**kwargs) -> pd.Series:
    return pd.Series(kwargs)


def test_venue_weights_normalizes_and_drops_missing():
    row = _row(
        binance__price_weight=2.0, binance__price_mid=100.0,
        bybit__price_weight=1.0, bybit__price_mid=100.0,
        hyperliquid__price_weight=np.nan, hyperliquid__price_mid=100.0,
    )
    weights = _venue_weights(row, ("binance", "bybit", "hyperliquid"))
    assert weights == pytest.approx({"binance": 2 / 3, "bybit": 1 / 3})


def test_gross_bps_long_buys_ask_sells_bid():
    entry = _row(binance__price_best_ask=100.0, binance__price_best_bid=99.9)
    exit_ = _row(binance__price_best_ask=101.0, binance__price_best_bid=100.9)
    weights = {"binance": 1.0}
    gross = _gross_bps(entry, exit_, weights, direction=1)
    # long: buy at entry ask (100.0), sell at exit bid (100.9) -> positive
    expected = 1e4 * np.log(100.9 / 100.0)
    assert gross == pytest.approx(expected)


def test_gross_bps_short_sells_bid_buys_ask():
    entry = _row(binance__price_best_ask=100.1, binance__price_best_bid=100.0)
    exit_ = _row(binance__price_best_ask=99.0, binance__price_best_bid=98.9)
    weights = {"binance": 1.0}
    gross = _gross_bps(entry, exit_, weights, direction=-1)
    # short: sell at entry bid (100.0), buy back at exit ask (99.0) -> positive
    expected = 1e4 * np.log(100.0 / 99.0)
    assert gross == pytest.approx(expected)


def test_gross_bps_nan_when_a_leg_price_missing():
    # direction=1 (long) uses entry ask and exit bid -- NaN the one it actually reads.
    entry = _row(binance__price_best_ask=100.0, binance__price_best_bid=99.9)
    exit_ = _row(binance__price_best_ask=101.0, binance__price_best_bid=np.nan)
    gross = _gross_bps(entry, exit_, {"binance": 1.0}, direction=1)
    assert np.isnan(gross)


def test_fee_bps_is_weighted_average_of_taker_fees():
    weights = {"binance": 0.5, "bybit": 0.5}
    assert _fee_bps(weights) == pytest.approx(0.5 * 5.0 + 0.5 * 5.5)


def test_capacity_is_the_binding_leg_not_a_sum():
    # binance: thin leg, weight 0.5 -> implies only 2x its depth in total notional
    # bybit: deep leg, weight 0.5 -> implies a much larger total notional
    row = _row(
        binance__ask_depth_5bps=1.0, binance__price_best_ask=100.0,   # $100 depth, weight 0.5 -> cap $200
        bybit__ask_depth_5bps=1000.0, bybit__price_best_ask=100.0,     # $100,000 depth, weight 0.5 -> cap $200,000
    )
    weights = {"binance": 0.5, "bybit": 0.5}
    capacity = _leg_capacity_usd(row, weights, direction=1)
    assert capacity == pytest.approx(200.0)  # bottleneck leg (binance), not (200+200000)/2


def test_build_trades_uses_frozen_thresholds_not_recomputed_ones():
    from market_physics_v3.phase5_2_execution_economics import FrozenThresholds

    n = 2000
    rng = np.random.RandomState(3)
    asof = 1_000_000_000 + np.arange(n, dtype=np.int64) * 100_000_000
    feature = rng.uniform(-1, 1, n)
    mid = 100 + np.cumsum(rng.normal(0, 0.01, n))
    frame = pd.DataFrame({
        "asof_ns": asof,
        "symbol": "BTCUSDT",
        "okx__queue_imbalance_l5": feature,
        "okx__depth_fresh": True,
    })
    for v in ("binance", "bybit", "hyperliquid"):
        frame[f"{v}__price_weight"] = 1.0
        frame[f"{v}__price_mid"] = mid
        frame[f"{v}__price_best_bid"] = mid - 0.01
        frame[f"{v}__price_best_ask"] = mid + 0.01
        frame[f"{v}__ask_depth_5bps"] = 1000.0
        frame[f"{v}__bid_depth_5bps"] = 1000.0

    # Thresholds far outside the frame's own feature range -> build_trades must
    # produce ZERO trades if it's honoring the frozen thresholds rather than
    # recomputing its own 10/90 percentile off this frame's data (which would
    # always find *some* top/bottom decile regardless of the frozen values).
    impossible = FrozenThresholds(lo=-100.0, hi=100.0)
    assert build_trades(frame, "BTCUSDT", impossible, cadence_ms=100) == []


def test_summarize_trades_fill_rate_is_computed_from_capacity_not_hardcoded():
    trades = [
        Trade(
            symbol="BTCUSDT", entry_idx=0, exit_idx=300, direction=1, entry_asof_ns=i,
            weights={"binance": 1.0}, gross_bps=1.0, gross_mid_bps=1.0, fee_bps=5.0,
            capacity_usd=100_000.0,  # below REFERENCE_NOTIONAL_USD -> fill_rate must be < 1
            delayed_gross_bps=1.0, delayed_fee_bps=5.0,
        )
        for i in range(10)
    ]
    summary = summarize_trades(trades)
    assert summary["fill_rate"] == pytest.approx(0.5)  # 100_000 / 200_000
