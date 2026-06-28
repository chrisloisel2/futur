"""tests/test_carry_gate_v2.py — règles CARRY_GATE_V2 (Phase 7)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.carry_basis.carry_gate_v2 import (
    CarryGateV2, CarryGateV2Status as S, CarryGateV2Reason as R,
)

TS = pd.Timestamp("2024-06-01T08:00:00Z")


def _gate_with_panel(fb, fy, disp, flip):
    g = CarryGateV2([])                       # pas de data → panels vides
    idx = pd.DatetimeIndex([TS])
    g._panels["BTCUSDT"] = pd.DataFrame(
        {"fb": [fb], "fy": [fy], "abs_spread": [abs(fy - fb)], "disp_pct": [disp], "flip_risk": [flip]},
        index=idx)
    return g


def test_allow_positive_both_low_dispersion():
    d = _gate_with_panel(1e-4, 1.2e-4, 0.5, False).evaluate("BTCUSDT", TS)
    assert d.status == S.ALLOW and d.carry_size_multiplier == 1.0


def test_block_negative_binance():
    d = _gate_with_panel(-1e-5, 1e-4, 0.4, False).evaluate("BTCUSDT", TS)
    assert d.status == S.BLOCK and d.reason == R.NEGATIVE_BINANCE_FUNDING


def test_block_negative_bybit():
    d = _gate_with_panel(1e-4, -1e-5, 0.4, False).evaluate("BTCUSDT", TS)
    assert d.status == S.BLOCK and d.reason == R.NEGATIVE_BYBIT_FUNDING


def test_reduce_on_p91_dispersion():
    d = _gate_with_panel(1e-4, 1.2e-4, 0.91, False).evaluate("BTCUSDT", TS)
    assert d.status == S.REDUCE and d.carry_size_multiplier == 0.5


def test_block_on_p96_dispersion():
    d = _gate_with_panel(1e-4, 1.2e-4, 0.96, False).evaluate("BTCUSDT", TS)
    assert d.status == S.BLOCK and d.reason == R.HIGH_FUNDING_DISPERSION


def test_flip_risk_does_not_block_entry():
    # flip_risk calculé mais ne bloque PAS (anti-churn) : funding positif + low disp → ALLOW
    d = _gate_with_panel(1e-4, 1.2e-4, 0.3, True).evaluate("BTCUSDT", TS)
    assert d.status == S.ALLOW and d.flip_risk is True


def test_hard_block_only_on_negative_funding():
    g = _gate_with_panel(1e-4, 1.2e-4, 0.96, False)   # dispersion BLOCK mais funding positif
    assert g.evaluate("BTCUSDT", TS).status == S.BLOCK      # entrée bloquée (dispersion)
    assert g.hard_block("BTCUSDT", TS) is False             # mais pas de sortie forcée (funding +)
    g2 = _gate_with_panel(-1e-5, 1.2e-4, 0.3, False)
    assert g2.hard_block("BTCUSDT", TS) is True             # funding négatif → sortie


def test_block_missing_funding():
    g = CarryGateV2([])   # panels vides
    d = g.evaluate("BTCUSDT", TS)
    assert d.status == S.BLOCK and d.reason == R.MISSING_FUNDING


def test_causal_build_no_lookahead(tmp_path, monkeypatch):
    import src.institutional.engines.carry_basis.carry_gate_v2 as G
    monkeypatch.setattr(G, "BACKFILL", tmp_path)
    idx = pd.date_range("2024-01-01", periods=120, freq="8h", tz="UTC")
    for ex, val in (("binance", 1e-4), ("bybit", 1.2e-4)):
        p = tmp_path / ex / "funding" / "BTCUSDT.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"timestamp": idx, "funding_rate": np.full(120, val)}).to_parquet(p, index=False)
    g = G.CarryGateV2(["BTCUSDT"])
    panel = g._panels["BTCUSDT"]
    # disp_pct au début = NaN (pas assez d'historique) = pas de lookahead global
    assert pd.isna(panel["disp_pct"].iloc[0])
    # funding positif constant des deux côtés → ALLOW à la fin
    assert g.evaluate("BTCUSDT", idx[-1]).status == S.ALLOW
