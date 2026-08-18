import numpy as np
import pandas as pd

from alpha_foundry_v5.labs.plugins import OptionsPlugin
from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.support_audit import (
    _clock_change_mask,
    _match_columns,
    run_mechanism_support_audit,
)
from alpha_foundry_v5.support_io import (
    load_projected_support_frame,
    parquet_union_schema,
    support_projection_columns,
)


def test_support_audit_ignores_target_columns_when_matching_features():
    cols = [
        "target_future_return",
        "binance__price_dislocation_bps",
        "bybit__price_dislocation_bps",
    ]
    matched = _match_columns(cols, ["*price_dislocation_bps", "target_*"])
    assert "target_future_return" not in matched
    assert matched == ["binance__price_dislocation_bps", "bybit__price_dislocation_bps"]


def test_clock_support_counts_new_arrivals_not_forward_filled_rows():
    frame = pd.DataFrame({
        "symbol": ["BTCUSDT"] * 8,
        "clock": [100, 100, 100, 200, 200, 300, 300, 300],
    })
    mask, weight = _clock_change_mask(frame, "clock")
    assert int(mask.sum()) == 3
    assert float(weight.sum()) == 3.0


def test_options_plugin_is_strictly_option_namespaced():
    frame = pd.DataFrame({
        "asof_ns": [100, 200, 300],
        "deriv__iv_fake": [1.0, 2.0, 3.0],
        "other_gamma": [4.0, 5.0, 6.0],
        "option__iv_atm": [0.5, 0.6, 0.7],
        "option__gamma": [0.1, 0.2, 0.3],
    })
    spec = LabRegistry().spec("A14")
    out = OptionsPlugin().build_features(frame, spec)
    assert "option__iv_atm" in out
    assert "option__gamma" in out
    assert "deriv__iv_fake" not in out
    assert "other_gamma" not in out


def test_a3_strong_support_uses_exact_100ms_event_bins_and_no_target():
    n_per_symbol = 1200
    symbols = np.repeat(["BTCUSDT", "ETHUSDT"], n_per_symbol)
    local = np.tile(np.arange(n_per_symbol), 2)
    asof = local.astype(np.int64) * 100_000_000 + 1_000_000_000
    rng = np.random.default_rng(17)
    frame = pd.DataFrame({
        "asof_ns": asof,
        "symbol": symbols,
        "price_fair_value": 100.0 + np.sin(local / 17.0) + local * 1e-5,
        "binance__bid_remove_count_100ms": np.ones(len(symbols)),
        "bybit__ask_remove_count_100ms": np.ones(len(symbols)),
        "binance__trade_count_100ms": np.ones(len(symbols)),
        "bybit__trade_count_100ms": np.ones(len(symbols)),
        "okx__trade_count_100ms": np.ones(len(symbols)),
        "binance__queue_imbalance_l5": rng.normal(size=len(symbols)),
        "target_poison": rng.normal(size=len(symbols)),
    })
    readiness = {"A3": {"ready": True}}
    result = run_mechanism_support_audit(frame, readiness, labs=("A3",))
    lab = result["labs"]["A3"]
    assert result["target_free"] is True
    assert result["target_columns_used"] == []
    assert lab["groups_pass"] is True
    assert lab["support_verdict"] == "STRONG_SUPPORT"
    assert lab["recommended_max_hypothesis_tests"] == 8
    groups = {x["name"]: x for x in lab["support_groups"]}
    assert groups["book_depletions"]["events_total"] == 4800.0
    assert groups["trade_events"]["events_total"] == 7200.0


def test_support_projection_prunes_irrelevant_columns_and_keeps_late_sparse_features(tmp_path):
    root = tmp_path / "tensor"
    root.mkdir()
    pd.DataFrame({
        "asof_ns": [100, 200],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "price_fair_value": [100.0, 100.1],
        "bybit__liquidation_available_ts_ns": [90, 190],
        "bybit__open_interest_change_pct": [0.01, 0.02],
        "bybit__buy_notional_10bps": [100000.0, 100000.0],
        "irrelevant_noise": [1.0, 2.0],
        "target_future_return": [9.0, 9.0],
    }).to_parquet(root / "part-00000.parquet", index=False)
    pd.DataFrame({
        "asof_ns": [300, 400],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "price_fair_value": [100.2, 100.3],
        "bybit__liquidation_available_ts_ns": [290, 390],
        "bybit__open_interest_change_pct": [0.03, 0.04],
        "bybit__buy_notional_10bps": [100000.0, 100000.0],
        # Sparse feature appears only in a later chunk.
        "bybit__liquidation_notional_30000ms": [1000.0, 2000.0],
        "irrelevant_noise": [3.0, 4.0],
        "target_future_return": [9.0, 9.0],
    }).to_parquet(root / "part-00001.parquet", index=False)

    parts, union, _ = parquet_union_schema(str(root))
    assert len(parts) == 2
    assert "bybit__liquidation_notional_30000ms" in union

    registry = LabRegistry()
    projection = support_projection_columns(union, ["A7"], registry)
    assert "bybit__liquidation_notional_30000ms" in projection
    assert "bybit__liquidation_available_ts_ns" in projection
    assert "irrelevant_noise" not in projection
    assert "target_future_return" not in projection

    frame, report = load_projected_support_frame(str(root), ["A7"], registry)
    assert len(frame) == 4
    assert report["logical_columns"] == len(union)
    assert report["loaded_columns"] < report["logical_columns"]
    assert report["pruned_columns"] > 0
    assert "bybit__liquidation_notional_30000ms" in frame.columns
    assert "irrelevant_noise" not in frame.columns
    assert "target_future_return" not in frame.columns
    assert frame["bybit__liquidation_notional_30000ms"].notna().sum() == 2
