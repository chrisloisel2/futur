"""
tests/test_paper_xvenue.py
─────────────────────────────────────────────────────────────────────────────
Sleeve paper FUNDING_XVENUE (scripts/run_paper_xvenue_v0.py) : différentiel
identique à la math gelée, stores idempotents, PnL compté uniquement après
paper_start, position héritée facturée une fois. Aucun réseau.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

_spec = importlib.util.spec_from_file_location(
    "rx", Path(__file__).parents[1] / "scripts" / "run_paper_xvenue_v0.py")
rx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rx)


def _mk_stores(tmp_path, hl_rate=0.0001, bn_rate=0.0002, hours=72):
    t0 = pd.Timestamp("2026-07-05T00:00:00Z")
    hl_idx = pd.date_range(t0 + pd.Timedelta("1h"), periods=hours, freq="1h")
    rx._write_store(tmp_path / "hyperliquid" / "BTC.parquet",
                    pd.Series(hl_rate, index=hl_idx))
    bn_idx = pd.date_range(t0 + pd.Timedelta("8h"), periods=hours // 8, freq="8h")
    rx._write_store(tmp_path / "binance" / "BTCUSDT.parquet",
                    pd.Series(bn_rate, index=bn_idx))


def test_differential_matches_hand_computation(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "STORE", tmp_path)
    _mk_stores(tmp_path)
    d = rx.build_differential_live("BTC", "BTCUSDT")
    # chaque intervalle plein : 8 h × 1 bp HL − 2 bp Binance = 6 bp
    assert len(d) == 8
    assert np.allclose(d.values, 6.0)


def test_store_write_dedups_and_sorts(tmp_path):
    idx = pd.to_datetime(["2026-07-06T08:00Z", "2026-07-05T08:00Z",
                          "2026-07-06T08:00Z"], utc=True)
    p = tmp_path / "binance" / "X.parquet"
    rx._write_store(p, pd.Series([1.0, 2.0, 3.0], index=idx))
    out = rx._read_store(p)
    assert len(out) == 2
    assert out.index.is_monotonic_increasing
    assert out.iloc[-1] == 3.0          # keep='last'


def test_account_only_after_start_inherited_entry_billed():
    idx = pd.date_range("2026-07-05T08:00Z", periods=60, freq="8h")
    d = pd.Series(10.0, index=idx)      # S >> θ_in : position entrée dès le warm-up
    res = rx.run_rule(d, rx.PARAMS["lookback"], rx.PARAMS["theta_in_ann"],
                      rx.PARAMS["theta_out_ann"], 1.0, rx.RT_HL_BP)
    assert res["held"].iloc[-1] != 0
    start = idx[40]                     # position déjà tenue à ce stade
    net = rx.account_from_start(res, start, 1.0)
    assert (net.index > start).all()
    raw = res["net"][res["net"].index > start]
    assert np.isclose(net.iloc[0], raw.iloc[0] - rx.RT_HL_BP / 2.0)
    assert np.allclose(net.iloc[1:].values, raw.iloc[1:].values)


def test_account_flat_at_start_no_charge():
    idx = pd.date_range("2026-07-05T08:00Z", periods=60, freq="8h")
    d = pd.Series(0.0, index=idx)       # jamais de position
    res = rx.run_rule(d, rx.PARAMS["lookback"], rx.PARAMS["theta_in_ann"],
                      rx.PARAMS["theta_out_ann"], 1.0, rx.RT_HL_BP)
    net = rx.account_from_start(res, idx[30], 1.0)
    assert np.allclose(net.values, 0.0)
