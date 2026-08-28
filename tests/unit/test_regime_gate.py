"""tests/test_regime_gate.py — RegimeGate causal (Phase 39). No-lookahead est l'invariant clé."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.portfolio.regime_gate import (
    RegimeGate, PERMISSION_BY_REGIME, PERMISSION_SIZE_MULT,
)


def _series(n=8000, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="1H", tz="UTC")
    # longue phase haussière (au-delà du warmup 2160h) puis krach
    bull = min(6000, n * 3 // 4)
    drift = np.concatenate([np.full(bull, 0.0004), np.full(n - bull, -0.0007)])
    px = 100 * np.cumprod(1 + drift + rng.normal(0, 0.004, n))
    return pd.Series(px, index=idx)


def test_no_lookahead_regime_is_causal():
    """Régime à t identique qu'on calcule sur la série complète ou tronquée à t."""
    s = _series()
    full = RegimeGate().compute_regime_series(s)
    cut = 5000
    trunc = RegimeGate().compute_regime_series(s.iloc[:cut])
    common = full.index[:cut][2200:]   # après min_history
    assert (full.loc[common].values == trunc.loc[common].values).mean() > 0.99


def test_permission_mapping():
    assert PERMISSION_BY_REGIME["BULL"] == "ALLOW_LONG"
    assert PERMISSION_BY_REGIME["BEAR"] == "BLOCK_LONG"
    assert PERMISSION_BY_REGIME["UNKNOWN"] == "BLOCK_LONG"
    assert PERMISSION_SIZE_MULT["BLOCK_LONG"] == 0.0


def test_unknown_before_history():
    s = _series(n=3000)
    g = RegimeGate()
    g.compute_regime_series(s)
    d = g.decide_at(s.index[100])  # avant min_history (2160h) → UNKNOWN/BLOCK
    assert d.btc_regime == "UNKNOWN" and d.size_mult == 0.0


def test_detects_bull_and_bear_phases():
    s = _series()
    reg = RegimeGate().compute_regime_series(s)
    tail_bear = reg.iloc[-500:]
    assert (tail_bear.isin(["BEAR", "CRASH"])).mean() > 0.3            # krach détecté
    assert (reg.iloc[2200:5500].isin(["BULL", "RECOVERY"])).mean() > 0.3  # bull post-warmup
