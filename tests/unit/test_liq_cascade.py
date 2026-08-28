"""tests/test_liq_cascade.py — détecteur de cascades 5-min + dataset causal."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.institutional.engines.liq_cascade.detector import CascadeConfig, detect_cascades
from src.institutional.engines.liq_cascade import dataset as DS


def _mk(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    oi = pd.Series(100_000 * np.cumprod(1 + rng.normal(0, 3e-4, n)), index=idx)
    px = pd.Series(50_000 * np.cumprod(1 + rng.normal(0, 5e-4, n)), index=idx)
    return pd.DataFrame({
        "create_time": idx,
        "sum_open_interest": oi.values,
        "sum_open_interest_value": (oi * px).values,
        "sum_taker_long_short_vol_ratio": rng.normal(1, 0.05, n),
        "sum_toptrader_long_short_ratio": rng.normal(1.2, 0.05, n),
        "count_long_short_ratio": rng.normal(1.4, 0.05, n),
    }).reset_index(drop=True)


def _inject_cascade(df, i0, bars=6, oi_drop=0.06, px_drop=0.05):
    df = df.copy()
    for j in range(bars):
        f_oi = 1 - oi_drop * (j + 1) / bars
        f_px = 1 - px_drop * (j + 1) / bars
        df.loc[i0 + j, "sum_open_interest"] *= f_oi
        df.loc[i0 + j:, "sum_open_interest_value"] = (
            df.loc[i0 + j:, "sum_open_interest"] * 50_000 * f_px)
    return df


def test_detector_finds_injected_cascade():
    df = DS.load_metrics  # silence lints
    d = _inject_cascade(_mk(), i0=4000)
    d["px"] = d["sum_open_interest_value"] / d["sum_open_interest"]
    ev = detect_cascades(d, CascadeConfig(min_warmup_bars=864))
    assert len(ev) >= 1
    hit = ev[(ev.row >= 4000) & (ev.row <= 4010)]
    assert len(hit) >= 1 and hit.iloc[0]["kind"] == "LONG_CASCADE"


def test_detector_silent_when_calm():
    d = _mk()
    d["px"] = d["sum_open_interest_value"] / d["sum_open_interest"]
    ev = detect_cascades(d, CascadeConfig(min_warmup_bars=864))
    assert len(ev) == 0


def test_detector_no_event_during_warmup():
    d = _inject_cascade(_mk(), i0=200)   # cascade AVANT le warm-up
    d["px"] = d["sum_open_interest_value"] / d["sum_open_interest"]
    ev = detect_cascades(d, CascadeConfig(min_warmup_bars=864))
    assert all(ev.row >= 864) if len(ev) else True


def test_dataset_features_causal(monkeypatch, tmp_path):
    """Modifier le FUTUR (après l'event) ne change AUCUNE feature de l'event."""
    d = _inject_cascade(_mk(), i0=4000)
    d2 = d.copy()
    # choc futur DANS la fenêtre de label 8h (96 barres) mais APRÈS l'event
    d2.loc[4050:, "sum_open_interest"] *= 0.5
    d2.loc[4050:, "sum_taker_long_short_vol_ratio"] += 3.0

    def fake_load(sym, _cache={}):
        base = d if sym == "A" else d2
        out = base.copy()
        out["px"] = out["sum_open_interest_value"] / out["sum_open_interest"]
        return out

    monkeypatch.setattr(DS, "load_metrics", lambda s: fake_load(s))
    ev1 = DS.build_event_dataset(["A"])
    ev2 = DS.build_event_dataset(["B"])
    e1 = ev1[(ev1.row >= 4000) & (ev1.row <= 4010)].iloc[0]
    e2 = ev2[(ev2.row >= 4000) & (ev2.row <= 4010)].iloc[0]
    for f in DS.FEATURES_V2:
        a, b = e1[f], e2[f]
        assert (pd.isna(a) and pd.isna(b)) or a == b, f"feature non-causale: {f}"
    # mais les LABELS, eux, doivent changer (ils regardent le futur)
    assert e1["fwd_8h"] != e2["fwd_8h"]


def test_dataset_entry_at_next_bar():
    """Le label part de la barre row+1 (pas du prix de détection)."""
    d = _inject_cascade(_mk(), i0=4000)
    d["px"] = d["sum_open_interest_value"] / d["sum_open_interest"]
    ev = detect_cascades(d, CascadeConfig(min_warmup_bars=864))
    row = int(ev.iloc[0]["row"])
    px = d["px"].values
    expected = np.log(px[min(row + 1 + 12, len(px) - 1)] / px[row + 1])
    import src.institutional.engines.liq_cascade.dataset as DSm
    import unittest.mock as um
    with um.patch.object(DSm, "load_metrics", lambda s: d):
        ds = DSm.build_event_dataset(["X"])
    got = ds[ds.row == row].iloc[0]["fwd_1h"]
    assert abs(got - expected) < 1e-12
