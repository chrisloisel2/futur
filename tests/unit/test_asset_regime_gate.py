"""tests/test_asset_regime_gate.py — Asset Regime Gate (Phase 47), causal."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.portfolio.asset_regime_gate import AssetRegimeGate


def _trend(n, slope, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="1H", tz="UTC")
    return pd.Series(100 * np.cumprod(1 + slope + rng.normal(0, 0.003, n)), index=idx)


def test_block_when_btc_hostile_even_if_asset_bull():
    n = 5000
    btc = _trend(n, -0.0006, 0)      # BTC bear
    alt = _trend(n, 0.0005, 1)       # alt bull
    g = AssetRegimeGate().fit({"BTCUSDT": btc, "SOLUSDT": alt})
    d = g.decide_long("SOLUSDT", alt.index[-1])
    assert d.permission == "BLOCK_LONG"   # macro BTC bear bloque tout


def test_allow_when_both_bull():
    n = 6000
    btc = _trend(n, 0.0005, 0)
    alt = _trend(n, 0.0005, 2)
    g = AssetRegimeGate().fit({"BTCUSDT": btc, "SOLUSDT": alt})
    # vers la fin de la phase haussière
    d = g.decide_long("SOLUSDT", alt.index[5500])
    assert d.permission in ("ALLOW_LONG", "REDUCE_LONG")
    assert d.btc_regime in ("BULL", "RECOVERY")


def test_block_alt_when_btc_bull_but_alt_bear():
    n = 6000
    btc = _trend(n, 0.0006, 0)                       # BTC bull
    # alt : bull puis chute brutale (diverge de BTC) sur la fin
    alt = pd.concat([_trend(4500, 0.0006, 3), _trend(1500, -0.0015, 4) * 0.5])
    alt.index = btc.index
    g = AssetRegimeGate().fit({"BTCUSDT": btc, "SOLUSDT": alt})
    d = g.decide_long("SOLUSDT", alt.index[-1])
    # BTC bull mais alt cassé → réduit ou bloqué (pas full allow)
    assert d.permission in ("BLOCK_LONG", "REDUCE_LONG")


def test_asset_flip_exit_on_asset_hostile():
    n = 6000
    btc = _trend(n, 0.0006, 0)
    alt = pd.concat([_trend(4500, 0.0006, 3), _trend(1500, -0.002, 9) * 0.5])
    alt.index = btc.index
    g = AssetRegimeGate().fit({"BTCUSDT": btc, "SOLUSDT": alt})
    assert g.should_exit_long("SOLUSDT", alt.index[-1]) is True
