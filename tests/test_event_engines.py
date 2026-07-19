"""tests/test_event_engines.py — détecteurs CROWDING_REVERSAL + PREMIUM_DISLOCATION."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.crowding_reversal.detector import (
    WashoutConfig, detect_washouts)
import src.institutional.engines.premium_dislocation.detector as PD


def _mk(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    oi = pd.Series(100_000 * np.cumprod(1 + rng.normal(0, 3e-4, n)), index=idx)
    px = pd.Series(50_000 * np.cumprod(1 + rng.normal(0, 5e-4, n)), index=idx)
    d = pd.DataFrame({
        "create_time": idx, "symbol": "TESTUSDT",
        "sum_open_interest": oi.values,
        "sum_open_interest_value": (oi * px).values,
        "sum_taker_long_short_vol_ratio": rng.normal(1, 0.05, n),
        "sum_toptrader_long_short_ratio": rng.normal(1.2, 0.02, n),
        "count_long_short_ratio": rng.normal(1.4, 0.05, n),
    }).reset_index(drop=True)
    d["px"] = d["sum_open_interest_value"] / d["sum_open_interest"]
    return d


def test_washout_detected():
    d = _mk()
    # top traders capitulent + OI purgé sur 24h autour de i=4000
    d.loc[3712:4010, "sum_open_interest"] *= np.linspace(1, 0.90, 299)  # −10% / 24h
    d.loc[3990:4010, "sum_toptrader_long_short_ratio"] = 1.0            # z très bas
    ev = detect_washouts(d, WashoutConfig(min_warmup_bars=864))
    assert len(ev) >= 1
    # l'event tombe au 1er bar où purge 24h ET capitulation coïncident (le gap
    # 24h supprime les suivants) → fenêtre = toute la rampe de purge
    hit = ev[(ev.row >= 3700) & (ev.row <= 4015)]
    assert len(hit) >= 1 and hit.iloc[0]["kind"] == "CROWD_WASHOUT"


def test_washout_silent_when_calm():
    assert len(detect_washouts(_mk(), WashoutConfig(min_warmup_bars=864))) == 0


def test_washout_respects_min_gap():
    d = _mk()
    d.loc[3712:4300, "sum_open_interest"] *= 0.90
    d.loc[3990:4300, "sum_toptrader_long_short_ratio"] = 1.0
    ev = detect_washouts(d, WashoutConfig(min_warmup_bars=864))
    if len(ev) >= 2:
        assert (ev["row"].diff().dropna() >= 288).all()


def test_premium_capitulation_detected(monkeypatch):
    d = _mk()
    n = len(d)
    rng = np.random.default_rng(1)
    prem = pd.DataFrame({"ts": d["create_time"],
                         "premium": rng.normal(0.0001, 0.00005, n),
                         "premium_low": 0.0})
    prem.loc[4000:4004, "premium"] = -0.003    # −30 bps : capitulation perp
    monkeypatch.setattr(PD, "load_premium", lambda s: prem)
    ev = PD.detect_premium_dislocations(d, PD.PremiumConfig(min_warmup_bars=864))
    hit = ev[(ev.row >= 4000) & (ev.row <= 4006)]
    assert len(hit) >= 1 and hit.iloc[0]["kind"] == "PREM_CAPITULATION"
    assert hit.iloc[0]["prem_at"] <= -0.001


def test_ignition_detected():
    from src.institutional.engines.flow_ignition.detector import (
        IgnitionConfig, detect_ignitions)
    d = _mk()
    # expansion OI violente + taker acheteur + thrust prix à i=4000-4005
    for j in range(6):
        d.loc[4000 + j, "sum_open_interest"] *= 1 + 0.01 * (j + 1)
        d.loc[4000 + j:, "sum_open_interest_value"] = (
            d.loc[4000 + j:, "sum_open_interest"] * 50_000 * (1 + 0.01 * (j + 1)))
    d.loc[4000:4005, "sum_taker_long_short_vol_ratio"] = 1.5
    d["px"] = d["sum_open_interest_value"] / d["sum_open_interest"]
    ev = detect_ignitions(d, IgnitionConfig(min_warmup_bars=864))
    hit = ev[(ev.row >= 4000) & (ev.row <= 4008)]
    assert len(hit) >= 1 and hit.iloc[0]["kind"] == "FLOW_IGNITION"
    assert hit.iloc[0]["oi_drop_30m"] > 0        # expansion, pas compression


def test_ignition_silent_when_calm():
    from src.institutional.engines.flow_ignition.detector import (
        IgnitionConfig, detect_ignitions)
    d = _mk()
    assert len(detect_ignitions(d, IgnitionConfig(min_warmup_bars=864))) == 0


def test_spillover_detected(monkeypatch):
    import src.institutional.engines.btc_spillover.detector as SP
    d = _mk()   # l'alt : plat autour de i=4000
    # BTC : thrust +2% sur la fenêtre 1h finissant aux barres 4000+
    idx = d["create_time"]
    btc_ret = pd.Series(0.0, index=range(len(d)))
    btc_ret.iloc[4000:4010] = 0.02
    monkeypatch.setattr(SP, "_btc_frame", lambda: pd.DataFrame(
        {"t": idx, "btc_ret_1h_lead": btc_ret.values}))
    ev = SP.detect_spillovers(d, SP.SpilloverConfig(min_warmup_bars=864))
    hit = ev[(ev.row >= 4000) & (ev.row <= 4010)]
    assert len(hit) >= 1
    assert hit.iloc[0]["lag_gap"] > 0.01          # l'alt est bien en retard


def test_spillover_excludes_btc(monkeypatch):
    import src.institutional.engines.btc_spillover.detector as SP
    d = _mk()
    d["symbol"] = "BTCUSDT"
    assert len(SP.detect_spillovers(d)) == 0


def test_premium_silent_when_flat(monkeypatch):
    d = _mk()
    prem = pd.DataFrame({"ts": d["create_time"],
                         "premium": np.full(len(d), 0.0001),
                         "premium_low": 0.0})
    monkeypatch.setattr(PD, "load_premium", lambda s: prem)
    ev = PD.detect_premium_dislocations(d, PD.PremiumConfig(min_warmup_bars=864))
    assert len(ev) == 0
