"""tests/test_cross_exchange.py — cross-exchange funding signal logic (sur fixtures)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import src.institutional.data.derivatives.cross_exchange as CX


def _mk(tmp, exchange, sym, base, n=120):
    idx = pd.date_range("2026-01-01", periods=n, freq="8h", tz="UTC")
    df = pd.DataFrame({"timestamp": idx, "funding_rate": base})
    p = tmp / exchange / "funding" / f"{sym}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)


def test_cross_exchange_spread_and_consensus(tmp_path, monkeypatch):
    monkeypatch.setattr(CX, "BACKFILL", tmp_path)
    n = 120
    _mk(tmp_path, "binance", "BTCUSDT", np.full(n, 1e-4))
    _mk(tmp_path, "bybit", "BTCUSDT", np.full(n, 0.5e-4))
    _mk(tmp_path, "okx", "BTCUSDT", np.full(n, 0.0))
    df = CX.cross_exchange_funding("BTCUSDT")
    assert {"binance", "bybit", "okx", "spread", "consensus"} <= set(df.columns)
    # spread = max-min = 1e-4 - 0 = 1e-4 ; consensus = moyenne = 0.5e-4
    assert abs(df["spread"].iloc[-1] - 1e-4) < 1e-9
    assert abs(df["consensus"].iloc[-1] - 0.5e-4) < 1e-9
    assert abs(df["binance_okx"].iloc[-1] - 1e-4) < 1e-9


def test_divergence_zscore_spikes(tmp_path, monkeypatch):
    monkeypatch.setattr(CX, "BACKFILL", tmp_path)
    n = 120
    b = np.full(n, 1e-4); b[-1] = 5e-4    # spike binance
    _mk(tmp_path, "binance", "ETHUSDT", b)
    _mk(tmp_path, "bybit", "ETHUSDT", np.full(n, 1e-4))
    df = CX.funding_divergence_signal("ETHUSDT")
    assert "spread_zscore" in df.columns
    assert df["spread_zscore"].iloc[-1] > 2.0   # dislocation détectée


def test_no_panel_when_single_exchange(tmp_path, monkeypatch):
    monkeypatch.setattr(CX, "BACKFILL", tmp_path)
    _mk(tmp_path, "binance", "SOLUSDT", np.full(120, 1e-4))
    assert CX.cross_exchange_funding("SOLUSDT").empty  # 1 exchange → pas de cross-exchange
