"""tests/test_live_metrics_tail.py — raccordement de la queue fraîche sur le
détecteur de cascades (2026-09-05).

Contexte : `detector.METRICS_DIR` est un backfill d'archives QUOTIDIENNES
Binance Vision, en retard structurel de 1 à 2 jours. Mesuré avant correctif :
la famille cascade découvrait ses événements 45-48 h après coup pour un horizon
de 4 h, soit 100 % de décisions périmées à l'arrivée. `_append_live_tail`
prolonge la série avec les barres collectées par
`scripts/collect_oi_metrics_5m.py`.

Ce qui doit rester vrai, quoi qu'il arrive ensuite :
  - Vision fait FOI sur le recouvrement (une barre déjà servie à un détecteur
    ne doit jamais changer de valeur, sinon une décision passée cesse d'être
    reproductible) ;
  - l'absence de queue live redonne EXACTEMENT l'ancien comportement ;
  - un fichier live illisible ne casse pas la lecture ;
  - chaque endpoint garde SON décalage d'horodatage mesuré (un décalage
    uniforme produirait une série silencieusement fausse).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import scripts.collect_oi_metrics_5m as collector
from src.institutional.engines.liq_cascade import detector

COLS = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
        "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
        "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]


def _frame(start: str, n: int, oi_base: float = 100.0) -> pd.DataFrame:
    t = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "create_time": t, "symbol": "TESTUSDT",
        "sum_open_interest": [oi_base + i for i in range(n)],
        "sum_open_interest_value": [(oi_base + i) * 1000.0 for i in range(n)],
        "count_toptrader_long_short_ratio": [1.0] * n,
        "sum_toptrader_long_short_ratio": [1.0] * n,
        "count_long_short_ratio": [1.0] * n,
        "sum_taker_long_short_vol_ratio": [1.0] * n,
    })[COLS]


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    vision, live = tmp_path / "vision", tmp_path / "live"
    vision.mkdir(); live.mkdir()
    monkeypatch.setattr(detector, "METRICS_DIR", vision)
    monkeypatch.setattr(detector, "LIVE_METRICS_DIR", live)
    return vision, live


def test_tail_extends_the_series(dirs):
    vision, live = dirs
    _frame("2026-09-01T00:00Z", 12).to_parquet(vision / "TESTUSDT_metrics_5m.parquet", index=False)
    _frame("2026-09-01T01:00Z", 12, oi_base=200.0).to_parquet(
        live / "TESTUSDT_metrics_5m_live.parquet", index=False)
    d = detector.load_metrics("TESTUSDT")
    assert len(d) == 24
    assert d["create_time"].max() == pd.Timestamp("2026-09-01T01:55Z")
    assert d["create_time"].is_monotonic_increasing


def test_vision_wins_on_the_overlap(dirs):
    """Une barre republiée par l'API ne doit PAS réécrire celle que Vision
    a déjà servie : une décision passée resterait sinon irreproductible."""
    vision, live = dirs
    _frame("2026-09-01T00:00Z", 12).to_parquet(vision / "TESTUSDT_metrics_5m.parquet", index=False)
    # même fenêtre, valeurs différentes, plus 6 barres réellement neuves
    _frame("2026-09-01T00:00Z", 18, oi_base=999.0).to_parquet(
        live / "TESTUSDT_metrics_5m_live.parquet", index=False)
    d = detector.load_metrics("TESTUSDT")
    assert len(d) == 18
    overlap = d[d["create_time"] < pd.Timestamp("2026-09-01T01:00Z")]
    assert overlap["sum_open_interest"].tolist() == [100.0 + i for i in range(12)]
    assert d["sum_open_interest"].iloc[12] == 999.0 + 12   # la queue, elle, vient du live


def test_no_live_file_reproduces_the_old_behaviour(dirs):
    vision, _ = dirs
    _frame("2026-09-01T00:00Z", 12).to_parquet(vision / "TESTUSDT_metrics_5m.parquet", index=False)
    d = detector.load_metrics("TESTUSDT")
    assert len(d) == 12
    assert d["create_time"].max() == pd.Timestamp("2026-09-01T00:55Z")


def test_unreadable_live_file_falls_back_to_vision(dirs):
    """Le collecteur écrit pendant que le détecteur lit : un parquet tronqué
    doit dégrader vers Vision seul, jamais faire échouer le producteur."""
    vision, live = dirs
    _frame("2026-09-01T00:00Z", 12).to_parquet(vision / "TESTUSDT_metrics_5m.parquet", index=False)
    (live / "TESTUSDT_metrics_5m_live.parquet").write_bytes(b"pas un parquet")
    d = detector.load_metrics("TESTUSDT")
    assert len(d) == 12


def test_missing_vision_file_still_returns_none(dirs):
    """La queue live seule ne suffit pas : sans historique, les fenêtres
    glissantes du z-score n'ont pas de quoi se calibrer."""
    _, live = dirs
    _frame("2026-09-01T00:00Z", 12).to_parquet(live / "TESTUSDT_metrics_5m_live.parquet", index=False)
    assert detector.load_metrics("TESTUSDT") is None


def test_px_is_recomputed_over_the_spliced_series(dirs):
    vision, live = dirs
    _frame("2026-09-01T00:00Z", 12).to_parquet(vision / "TESTUSDT_metrics_5m.parquet", index=False)
    _frame("2026-09-01T01:00Z", 6, oi_base=200.0).to_parquet(
        live / "TESTUSDT_metrics_5m_live.parquet", index=False)
    d = detector.load_metrics("TESTUSDT")
    assert d["px"].notna().all()
    assert (d["px"] == 1000.0).all()    # oi_value/oi par construction du fixture


def test_each_endpoint_keeps_its_measured_offset():
    """Décalages MESURÉS contre l'archive Vision (balayage −15..+15 min), pas
    supposés. Les confondre casse la série en silence : sur l'OI, l'erreur
    médiane passe de 0,000000 à 76,2 unités ; sur le ratio taker, de 0,000123
    à 0,45."""
    offsets = {col: off for col, (_, _, off) in collector.FIELDS.items()}
    assert offsets["sum_open_interest"] == -5
    assert offsets["sum_open_interest_value"] == -5
    assert offsets["sum_taker_long_short_vol_ratio"] == 0      # la seule à 0
    assert offsets["sum_toptrader_long_short_ratio"] == -5
    assert offsets["count_long_short_ratio"] == -5
    assert offsets["count_toptrader_long_short_ratio"] == -5


def test_collector_writes_the_vision_schema():
    """Le concat de _append_live_tail suppose des colonnes identiques."""
    assert collector.VISION_COLUMNS == COLS


def test_collector_disk_floor_matches_the_cycle():
    import scripts.run_live_alpha_lab_cycle as cycle
    assert collector.MIN_FREE_DISK_GB == cycle.MIN_FREE_DISK_GB


def test_collector_runs_before_producers_in_the_cycle():
    src = (ROOT / "scripts" / "run_live_alpha_lab_cycle.py").read_text()
    import scripts.run_live_alpha_lab_cycle as cycle
    assert cycle.COLLECTOR_SCRIPT == "scripts/collect_oi_metrics_5m.py"
    # l'appel run_step du collecteur doit précéder la boucle des producteurs
    assert src.index("COLLECTOR_SCRIPT, ") < src.index("for r in runners:")
