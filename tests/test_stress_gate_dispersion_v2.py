"""
tests/test_stress_gate_dispersion_v2.py
─────────────────────────────────────────────────────────────────────────────
Phase 1 (intégrité du panel) de stress_gate_dispersion_v2_reproduction —
voir research/edge_factory/basis_dispersion/stress_gate_dispersion_v2/
PREREGISTRATION.md. Données synthétiques déterministes uniquement : ceci
prouve que le CODE est causal/fail-closed, pas que le signal est un edge
(Phase 2, qui requiert des données réelles absentes de cette machine —
voir research/forensics/stress_gate_c78874b/).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.backtest_stress_gate_dispersion_v2 import (
    PanelIntegrityError, build_panel, causal_expanding_quantile,
    forward_drawdown, panel_manifest)


def _ts(n, start="2024-01-01", freq="8h"):
    return pd.date_range(start, periods=n, freq=freq, tz="UTC")


def test_no_future_rows_used():
    """Un pic isolé à l'instant T ne doit pas influencer son PROPRE seuil
    (le shift(1) doit l'exclure de sa propre fenêtre)."""
    idx = _ts(400)
    vals = np.full(400, 1.0)
    spike_at = 300
    vals[spike_at] = 1000.0
    s = pd.Series(vals, index=idx)
    thr = causal_expanding_quantile(s)
    # Seuil recalculé manuellement à spike_at à partir des 270 valeurs
    # STRICTEMENT antérieures (donc n'incluant PAS le pic lui-même) :
    manual = np.quantile(vals[spike_at - 270:spike_at], 0.95)
    assert thr.iloc[spike_at] == pytest.approx(manual)
    assert thr.iloc[spike_at] < 1000.0   # le pic ne s'auto-dépasse pas


def test_causal_quantile_threshold():
    """Muter le futur ne doit JAMAIS changer un seuil déjà calculé dans le
    passé — preuve directe de causalité."""
    idx = _ts(500)
    rng = np.random.RandomState(0)
    vals = rng.rand(500)
    s = pd.Series(vals, index=idx)
    thr_before = causal_expanding_quantile(s).iloc[:300].copy()

    vals2 = vals.copy()
    vals2[300:] = 999.0          # mutation massive du futur seulement
    s2 = pd.Series(vals2, index=idx)
    thr_after = causal_expanding_quantile(s2).iloc[:300]

    pd.testing.assert_series_equal(thr_before, thr_after, check_names=False)


def test_no_forward_fill_across_venue_gap():
    """Un timestamp absent sur UNE venue doit disparaître du panel, jamais
    être comblé par la dernière valeur connue."""
    idx = _ts(20)
    binance = pd.Series(1.0, index=idx)
    bybit = pd.Series(1.0, index=idx).drop(idx[10])   # trou côté bybit
    panel = build_panel(binance, bybit)
    assert idx[10] not in panel.index
    assert len(panel) == 19


def test_duplicate_timestamp_rejected():
    idx = _ts(10)
    binance = pd.Series(1.0, index=idx)
    bybit = pd.Series(1.0, index=idx.append(idx[:1]))  # doublon
    with pytest.raises(PanelIntegrityError):
        build_panel(binance, bybit)


def test_missing_leg_handled_fail_closed():
    """Une venue totalement absente sur une fenêtre -> panel vide sur cette
    fenêtre, jamais une valeur inventée."""
    idx = _ts(10)
    binance = pd.Series(1.0, index=idx)
    bybit = pd.Series(dtype=float)   # jambe bybit totalement absente
    panel = build_panel(binance, bybit)
    assert len(panel) == 0


def test_panel_deterministic():
    idx = _ts(300)
    rng = np.random.RandomState(1)
    binance = pd.Series(rng.rand(300), index=idx)
    bybit = pd.Series(rng.rand(300), index=idx)
    p1 = build_panel(binance, bybit)
    p2 = build_panel(binance, bybit)
    pd.testing.assert_frame_equal(p1, p2)


def test_manifest_records_input_hashes():
    idx = _ts(300)
    rng = np.random.RandomState(2)
    binance = pd.Series(rng.rand(300), index=idx)
    bybit = pd.Series(rng.rand(300), index=idx)
    panel = build_panel(binance, bybit)
    m1 = panel_manifest(panel)
    m2 = panel_manifest(panel)
    assert m1["content_sha256"] == m2["content_sha256"]
    assert m1["n_rows"] == len(panel)

    mutated = panel.copy()
    mutated.iloc[0, 0] = mutated.iloc[0, 0] + 1.0
    m3 = panel_manifest(mutated)
    assert m3["content_sha256"] != m1["content_sha256"]


def test_forward_drawdown_uses_future_by_design_but_only_for_labels():
    """Le label EST autorisé à regarder le futur (creux forward) — ce test
    documente/verrouille ce contraste avec causal_expanding_quantile."""
    idx = _ts(10)
    price = pd.Series([100, 100, 90, 100, 100, 100, 100, 100, 100, 100],
                      index=idx, dtype=float)
    fwd = forward_drawdown(price, horizon_periods=2)
    # au t=0, fenetre [t+1, t+2] = [100, 90] -> creux = 90/100-1 = -10%
    assert fwd.iloc[0] == pytest.approx(-0.10)
