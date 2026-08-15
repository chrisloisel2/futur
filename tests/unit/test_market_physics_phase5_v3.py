import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from market_physics_v3.phase5_audit import add_targets, prepare_features, run_information_audit


def _frame(n=120, cadence_ms=100):
    t0 = 1_000_000_000_000
    idx = np.arange(n)
    frame = pd.DataFrame({
        "asof_ns": t0 + idx * cadence_ms * 1_000_000,
        "symbol": "BTCUSDT",
        "cadence_ms": cadence_ms,
        "price_ready": True,
        "price_fair_value": 100.0 * np.exp(idx * 1e-6),
        "price_dispersion_bps": np.sin(idx / 10.0),
        "binance__price_dislocation_bps": np.sin(idx / 11.0),
        "binance__price_microprice_offset_bps": np.cos(idx / 9.0),
        "binance__price_queue_imbalance_l1": np.sin(idx / 8.0),
        "binance__price_ofi_l1_grid": np.cos(idx / 7.0),
        "binance__price_spread_bps": 0.5,
        "binance__depth_fresh": idx % 2 == 0,
        "binance__queue_imbalance_l1": 0.5,
        "binance__queue_imbalance_l5": 0.4,
        "binance__queue_imbalance_l10": 0.3,
        "binance__ofi_l1_grid": 1.0,
        "binance__bid_depth_5bps": 10.0,
        "binance__ask_depth_5bps": 8.0,
        "binance__bid_depth_25bps": 20.0,
        "binance__ask_depth_25bps": 15.0,
        "binance__buy_notional_10bps": 1000.0,
        "binance__sell_notional_10bps": 1200.0,
        "binance__ask_weighted_distance_bps": 2.0,
        "binance__bid_weighted_distance_bps": 1.5,
    })
    return frame


def test_prepare_features_masks_stale_depth_but_not_price():
    frame, registry = prepare_features(_frame(), venues=("binance",))
    stale = ~frame["binance__depth_fresh"].astype(bool)
    assert frame.loc[stale, "binance__queue_imbalance_l1"].isna().all()
    assert frame.loc[stale, "binance__price_dislocation_bps"].notna().all()
    assert registry["binance__queue_imbalance_l1"] == "depth"
    assert registry["binance__price_dislocation_bps"] == "price"
    assert "binance__depth_5bps_imbalance" in registry


def test_add_targets_are_forward_and_causal():
    frame = add_targets(_frame(n=20), 100, horizons_ms=(100, 500))
    expected = 1e4 * np.log(frame.loc[1, "price_fair_value"] / frame.loc[0, "price_fair_value"])
    assert frame.loc[0, "target_100ms_bps"] == pytest.approx(expected)
    assert pd.isna(frame.iloc[-1]["target_100ms_bps"])
    assert pd.isna(frame.iloc[0]["past_100ms_bps"])


def test_phase5_refuses_short_tape_without_explicit_smoke_override():
    with pytest.raises(ValueError, match="DEV_PILOT minimum"):
        run_information_audit(
            _frame(n=120),
            cadence_ms=100,
            horizons_ms=(100, 500),
            venues=("binance",),
            min_duration_hours=6.0,
        )


def test_phase5_short_smoke_runs_without_alpha_verdict():
    out = run_information_audit(
        _frame(n=240),
        cadence_ms=100,
        horizons_ms=(100, 500),
        venues=("binance",),
        min_duration_hours=6.0,
        allow_short_smoke=True,
        block_shuffle_repeats=5,
        max_block_shortlist=2,
    )
    assert out["verdict"] == "SHORT_SMOKE_ONLY"
    assert out["test_count"] > 0
    assert out["general_candidates"] == 0


def test_phase5_cli_scripts_bootstrap_repo_root():
    root = Path(__file__).resolve().parents[2]
    for rel in [
        "scripts/build_market_physics_state_tape_stream_v3.py",
        "scripts/audit_market_physics_information_v3.py",
    ]:
        p = subprocess.run(
            [sys.executable, str(root / rel), "--help"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert p.returncode == 0, "%s failed: %s" % (rel, p.stderr)
