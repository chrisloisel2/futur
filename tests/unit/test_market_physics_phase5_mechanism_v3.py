import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from market_physics_v3.phase5_mechanism import run_mechanism_diagnostics


def _frame(n=600):
    rng = np.random.default_rng(7)
    cadence_ms = 100
    idx = np.arange(n)
    innovations = rng.normal(0.0, 2e-5, n)
    log_fv = np.cumsum(innovations)
    fair = 100.0 * np.exp(log_fv)
    spread = 1.0 + 0.2 * np.sin(idx / 17.0) + 0.05 * rng.normal(size=n)
    bybit_mid = fair * np.exp(rng.normal(0.0, 1e-6, n))
    okx_mid = fair * np.exp(rng.normal(0.0, 1e-6, n))
    hyper_mid = fair * np.exp(rng.normal(0.0, 1e-6, n))
    return pd.DataFrame({
        "asof_ns": 1_000_000_000_000 + idx * cadence_ms * 1_000_000,
        "symbol": "BTCUSDT",
        "cadence_ms": cadence_ms,
        "price_ready": True,
        "price_fair_value": fair,
        "binance__price_spread_bps": spread,
        "binance__price_mid": fair,
        "binance__price_weight": 0.25,
        "bybit__price_mid": bybit_mid,
        "bybit__price_weight": 0.25,
        "okx__price_mid": okx_mid,
        "okx__price_weight": 0.25,
        "hyperliquid__price_mid": hyper_mid,
        "hyperliquid__price_weight": 0.25,
    })


def _mechanisms():
    return pd.DataFrame([{
        "feature": "binance__price_spread_bps",
        "family": "price",
        "horizon_ms": 100,
        "median_ic": -0.05,
        "same_sign_symbols": 2,
        "candidate_symbols": 2,
        "classification": "GENERAL_CANDIDATE",
    }])


def test_phase5_1_mechanism_diagnostic_is_exploratory_and_has_loo_controls():
    result = run_mechanism_diagnostics(
        _frame(),
        _mechanisms(),
        cadence_ms=100,
        classifications=("GENERAL_CANDIDATE",),
    )
    assert result["summary"]["verdict"] == "EXPLORATORY_DEV_DIAGNOSTIC_ONLY"
    assert result["summary"]["causal_claim"] is False
    assert result["summary"]["economic_claim"] is False
    row = result["diagnostics"].iloc[0]
    assert row["venue"] == "binance"
    assert np.isfinite(row["loo_ic"])
    assert np.isfinite(row["partial_ic_controlling_past"])
    assert row["thirds_same_sign"] in (0, 1, 2, 3)
    assert "UNSIGNED_DIRECTIONAL_FEATURE" in row["confound_flags"]


def test_phase5_1_only_selects_requested_classification():
    mechanisms = _mechanisms()
    mechanisms.loc[0, "classification"] = "SINGLE_SYMBOL_WATCH"
    try:
        run_mechanism_diagnostics(_frame(), mechanisms, classifications=("GENERAL_CANDIDATE",))
    except ValueError as exc:
        assert "no mechanisms" in str(exc)
    else:
        raise AssertionError("expected selection failure")


def test_phase5_1_cli_bootstraps_repo_root():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts/audit_market_physics_mechanisms_v3.py"
    p = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert p.returncode == 0, p.stderr
