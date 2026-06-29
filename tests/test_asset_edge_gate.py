"""tests/test_asset_edge_gate.py — causalité + logique du gate edge réalisé par-actif."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.portfolio.asset_edge_gate import AssetEdgeGate


def _price(start, n, step):
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.Series([100.0 * (1 + step) ** i for i in range(n)], index=idx)


def _opp(asset, ts, h=8.0):
    return SimpleNamespace(asset=asset, timestamp=ts, expected_holding_hours=h)


def test_loser_alt_is_gated_out():
    # ALT en baisse monotone → edge net < 0 → jamais autorisé
    s = _price("2022-01-01", 24 * 400, -0.001)
    prices = {"ALTUSDT": s}
    opps = [_opp("ALTUSDT", ts) for ts in s.index[::24]]   # 1 signal/jour
    g = AssetEdgeGate(min_net=0.0, min_signals=20).fit(opps, prices, roundtrip_cost=0.0014)
    # 2023 décidé sur 2022 (perdant) → refusé
    assert g.allows("ALTUSDT", pd.Timestamp("2023-06-01", tz="UTC")) is False


def test_winner_alt_allowed_only_after_prior_proof():
    # ALT en hausse → edge net > 0, mais PAS avant d'avoir un historique
    s = _price("2022-01-01", 24 * 400, 0.002)
    prices = {"ALTUSDT": s}
    opps = [_opp("ALTUSDT", ts) for ts in s.index[::24]]
    g = AssetEdgeGate(min_net=0.0, min_signals=20).fit(opps, prices, roundtrip_cost=0.0014)
    # première année (2022) : aucun prior → refusé (causal, pas de lookahead)
    assert g.allows("ALTUSDT", pd.Timestamp("2022-03-01", tz="UTC")) is False
    # 2023 : prior 2022 gagnant → autorisé
    assert g.allows("ALTUSDT", pd.Timestamp("2023-03-01", tz="UTC")) is True


def test_thin_sample_blocked():
    s = _price("2022-01-01", 24 * 400, 0.002)
    prices = {"ALTUSDT": s}
    opps = [_opp("ALTUSDT", s.index[0]), _opp("ALTUSDT", s.index[24])]   # 2 signaux seulement
    g = AssetEdgeGate(min_net=0.0, min_signals=20).fit(opps, prices, roundtrip_cost=0.0014)
    assert g.allows("ALTUSDT", pd.Timestamp("2023-03-01", tz="UTC")) is False


def test_majors_exempt():
    g = AssetEdgeGate().fit([], {}, roundtrip_cost=0.0014)
    assert g.allows("BTCUSDT", pd.Timestamp("2023-01-01", tz="UTC")) is True
    assert g.allows("ETHUSDT", pd.Timestamp("2023-01-01", tz="UTC")) is True
