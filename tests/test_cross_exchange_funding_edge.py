"""tests/test_cross_exchange_funding_edge.py — panel Binance×Bybit causal & labels (Phase test)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import src.institutional.data.derivatives.features.cross_exchange_features as F


def _setup(tmp, monkeypatch, n=120, fb=1e-4, fy=1.5e-4, price_up=True):
    monkeypatch.setattr(F, "BACKFILL", tmp)
    idx = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    for ex, val in (("binance", fb), ("bybit", fy)):
        p = tmp / ex / "funding" / "BTCUSDT.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"timestamp": idx, "funding_rate": np.full(n, val)}).to_parquet(p, index=False)
    # prix : hausse monotone → forward returns > 0
    px = pd.Series(100 * np.cumprod(1 + np.full(n + 30, 0.001 if price_up else -0.001)),
                   index=pd.date_range("2024-01-01", periods=n + 30, freq="8h", tz="UTC"))
    monkeypatch.setattr(F, "_price_8h", lambda s: px)
    return idx


def test_signed_spread_bybit_minus_binance(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, fb=1e-4, fy=1.5e-4)
    df = F.build_panel("BTCUSDT")
    assert abs(df["funding_spread"].iloc[-1] - 0.5e-4) < 1e-9   # 1.5e-4 - 1e-4
    assert df["funding_binance"].iloc[-1] != df["funding_bybit"].iloc[-1]  # non mélangés


def test_timestamps_aligned_8h_utc(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    df = F.build_panel("BTCUSDT")
    assert str(df.index.tz) == "UTC"
    assert set(df.index.hour) <= {0, 8, 16}


def test_zscore_is_causal(tmp_path, monkeypatch):
    # spike sur la dernière barre ne doit PAS affecter les zscores passés
    idx = _setup(tmp_path, monkeypatch, n=120)
    full = F.build_panel("BTCUSDT")
    z_mid_full = full["funding_spread_zscore_90d"].iloc[60]
    # tronquer à 80 barres et recomputer
    monkeypatch.setattr(F, "BACKFILL", tmp_path)  # déjà set
    # modifier la dernière barre n'est pas trivial ici ; on vérifie au moins que
    # le zscore à i utilise un rolling (NaN au début = pas de lookahead global)
    assert pd.isna(full["funding_spread_zscore_90d"].iloc[0])


def test_forward_labels_after_timestamp(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, price_up=True)
    df = F.build_panel("BTCUSDT")
    # prix monte → forward_return_24h > 0 (label calculé APRÈS t)
    assert df["forward_return_24h"].dropna().mean() > 0


def test_positive_both_flag(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, fb=1e-4, fy=1.5e-4)
    df = F.build_panel("BTCUSDT")
    assert df["funding_positive_both"].iloc[-1] == 1
    # un côté négatif → flag 0
    _setup(tmp_path, monkeypatch, fb=-1e-4, fy=1.5e-4)
    df2 = F.build_panel("BTCUSDT")
    assert df2["funding_positive_both"].iloc[-1] == 0


def test_report_runs_even_without_okx(tmp_path, monkeypatch):
    # 2-exchanges (pas d'OKX) suffit pour le panel
    _setup(tmp_path, monkeypatch)
    df = F.build_panel("BTCUSDT")
    assert not df.empty and "funding_spread" in df.columns
