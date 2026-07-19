"""
tests/test_paper_mh_exec.py
─────────────────────────────────────────────────────────────────────────────
Replay d'exécution MH : prix d'arrivée strictement postérieur à la décision,
sortie à l'horizon moteur, coûts du fee_registry, tracking error vs labels.
Aucun réseau.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

_spec = importlib.util.spec_from_file_location(
    "rme", Path(__file__).parents[1] / "scripts" / "run_paper_mh_exec.py")
rme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rme)


def _mk_enriched(tmp_path, symbol="BTCUSDT"):
    idx = pd.date_range("2026-07-20T00:00Z", periods=48, freq="1h")
    close = pd.Series(100.0 + np.arange(48) * 0.5, index=idx)   # +50 bps/h
    df = pd.DataFrame({"datetime": idx, "close": close.values})
    (tmp_path / "enriched").mkdir(exist_ok=True)
    df.to_parquet(tmp_path / "enriched" / f"{symbol}_1h_enriched.parquet",
                  index=False)


def test_replay_decision_arrival_and_horizon(tmp_path, monkeypatch):
    monkeypatch.setattr(rme, "ENRICHED", tmp_path / "enriched")
    _mk_enriched(tmp_path)
    closes = rme._closes("BTCUSDT")
    row = {"event_time": pd.Timestamp("2026-07-20T02:10Z"),
           "engine": "LIQ_CASCADE"}
    net, entry_ts, exit_ts = rme.replay_decision(row, closes, cost_bp=14.0)
    assert entry_ts == pd.Timestamp("2026-07-20T03:00Z")   # strictement après
    assert exit_ts == pd.Timestamp("2026-07-20T07:00Z")    # +4 h (cascade)
    expected = (103.5 / 101.5 - 1) - 14.0 / 1e4
    assert abs(net - expected) < 1e-12
    # horizon 24 h pour crowding
    row["engine"] = "CROWDING_REVERSAL"
    _, _, exit_ts = rme.replay_decision(row, closes, cost_bp=14.0)
    assert exit_ts == pd.Timestamp("2026-07-21T03:00Z")
    # données manquantes → None
    row["event_time"] = pd.Timestamp("2026-07-25T00:00Z")
    assert rme.replay_decision(row, closes, 14.0) is None


def test_run_once_tracking_error(tmp_path, monkeypatch):
    monkeypatch.setattr(rme, "ENRICHED", tmp_path / "enriched")
    monkeypatch.setattr(rme, "OUT", tmp_path / "out")
    monkeypatch.setattr(rme.rmh, "SHADOW_LEDGER", tmp_path / "decisions.parquet")
    _mk_enriched(tmp_path)
    led = pd.DataFrame([
        {"event_time": pd.Timestamp("2026-07-20T02:10Z", tz=None),
         "symbol": "BTCUSDT", "engine": "LIQ_CASCADE",
         "horizon": "MH_consensus(fwd_1h,fwd_4h,fwd_8h)", "score": 0.8,
         "rank_pct": 0.8, "kind": "long", "tier": "book",
         "decided_at": "2026-07-20T02:15:00+00:00", "net_labeled": 0.018},
        {"event_time": pd.Timestamp("2026-07-20T05:10Z", tz=None),
         "symbol": "BTCUSDT", "engine": "LIQ_CASCADE",
         "horizon": "MH_consensus(fwd_1h,fwd_4h,fwd_8h)", "score": 0.9,
         "rank_pct": 0.9, "kind": "long", "tier": "book",
         "decided_at": "2026-07-20T05:15:00+00:00", "net_labeled": np.nan},
    ])
    led.to_parquet(tmp_path / "decisions.parquet", index=False)
    args = argparse.Namespace(capital=10000.0, paper_start="2026-07-19")
    state = rme.run_once(args)
    assert state["status"] == "active"
    assert state["n_decisions"] == 2 and state["n_replayed"] == 2
    assert state["cost_source"] in ("assumed", "api_signed")
    out = pd.read_parquet(tmp_path / "out" / "exec_ledger.parquet")
    assert np.isfinite(out["net_exec"]).all()
    assert np.isnan(out["net_label"].iloc[1])              # pending côté labels
    # TE calculable seulement avec ≥2 paires étiquetées → None ici
    assert state["tracking_error_vs_labels"] is None


def test_run_once_no_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(rme, "OUT", tmp_path / "out")
    monkeypatch.setattr(rme.rmh, "SHADOW_LEDGER", tmp_path / "absent.parquet")
    args = argparse.Namespace(capital=10000.0, paper_start="2026-07-19")
    assert rme.run_once(args)["status"] == "no_shadow_ledger"
