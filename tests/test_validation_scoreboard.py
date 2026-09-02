"""
tests/test_validation_scoreboard.py — mission ALPHA VALIDATION FACTORY,
section 26 (VALIDATION_AND_FORWARD_SCOREBOARD) and section 18 (edge
retention gated on sufficient forward N, never a ratio on too little data).
"""
from __future__ import annotations

import pandas as pd
import pytest

import scripts.compute_validation_scoreboard as sb


def test_edge_retention_insufficient_evidence_below_floor():
    assert sb._edge_retention(100.0, 60.0, 29) == "INSUFFICIENT_EVIDENCE"


def test_edge_retention_computed_at_or_above_floor():
    result = sb._edge_retention(100.0, 60.0, 30)
    assert result == "0.60"


def test_edge_retention_insufficient_when_forward_n_is_none():
    assert sb._edge_retention(100.0, 60.0, None) == "INSUFFICIENT_EVIDENCE"


def test_edge_retention_insufficient_when_historical_missing():
    assert sb._edge_retention(None, 60.0, 50) == "INSUFFICIENT_EVIDENCE"


def test_edge_retention_insufficient_when_historical_is_zero():
    """Diviser par zéro serait un crash, pas juste une valeur trompeuse --
    doit être explicitement INSUFFICIENT_EVIDENCE."""
    assert sb._edge_retention(0.0, 60.0, 50) == "INSUFFICIENT_EVIDENCE"


def test_forward_stats_no_decisions_file_returns_none_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "LAB_DIR", tmp_path)
    result = sb._forward_stats("NO_SUCH_ALPHA")
    assert result["forward_age_days"] is None
    assert result["forward_N_independent"] is None
    assert result["forward_N_raw"] == 0


def test_forward_stats_no_forward_decisions_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "LAB_DIR", tmp_path)
    d = tmp_path / "TEST_ALPHA"
    d.mkdir()
    df = pd.DataFrame([{"event_time": pd.Timestamp("2026-01-01", tz="UTC"), "symbol": "BTCUSDT",
                       "provenance": "REPLAY"}])
    df.to_parquet(d / "decisions.parquet", index=False)
    result = sb._forward_stats("TEST_ALPHA")
    assert result["forward_N_raw"] == 0
    assert result["forward_age_days"] is None


def test_forward_stats_counts_forward_only_not_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "LAB_DIR", tmp_path)
    d = tmp_path / "TEST_ALPHA"
    d.mkdir()
    df = pd.DataFrame([
        {"event_time": pd.Timestamp("2020-01-01", tz="UTC"), "symbol": "BTCUSDT", "provenance": "REPLAY"},
        {"event_time": pd.Timestamp.now(tz="UTC"), "symbol": "ETHUSDT", "provenance": "FORWARD_LIVE"},
    ])
    df.to_parquet(d / "decisions.parquet", index=False)
    result = sb._forward_stats("TEST_ALPHA")
    assert result["forward_N_raw"] == 1
    assert result["forward_age_days"] is not None
    assert result["forward_age_days"] < 1.0   # décidé "maintenant" dans ce test


def test_build_rows_reads_validation_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "VALIDATION_REGISTRY", tmp_path / "validation_registry.yaml")
    monkeypatch.setattr(sb, "LIVE_REGISTRY", tmp_path / "live_alpha_registry.yaml")
    monkeypatch.setattr(sb, "LAB_DIR", tmp_path / "lab")
    (tmp_path / "validation_registry.yaml").write_text("""
candidates:
  - candidate_id: TEST_CANDIDATE
    family: liquidation
    current_status: VALIDATED_FOR_FORWARD
    discovery_net_bps: 50.0
    validation_net_bps: 45.0
    n_validation_independent: 120
    validated_for_forward: true
""")
    (tmp_path / "live_alpha_registry.yaml").write_text("alphas: []")
    rows = sb.build_rows()
    assert len(rows) == 1
    assert rows[0]["alpha_id"] == "TEST_CANDIDATE"
    assert rows[0]["validated_for_forward"] is True
    assert rows[0]["validation_net_bps"] == 45.0
    assert rows[0]["N_validation_independent"] == 120


def test_build_rows_missing_fields_show_as_none_not_zero(tmp_path, monkeypatch):
    """Un champ pas encore rempli (validation pas encore lancée) doit rester
    None -- jamais un 0/False qui laisserait croire à un résultat réel."""
    monkeypatch.setattr(sb, "VALIDATION_REGISTRY", tmp_path / "validation_registry.yaml")
    monkeypatch.setattr(sb, "LIVE_REGISTRY", tmp_path / "live_alpha_registry.yaml")
    monkeypatch.setattr(sb, "LAB_DIR", tmp_path / "lab")
    (tmp_path / "validation_registry.yaml").write_text("""
candidates:
  - candidate_id: FRESH_CANDIDATE
    family: liquidation
    current_status: CANDIDATE
""")
    (tmp_path / "live_alpha_registry.yaml").write_text("alphas: []")
    rows = sb.build_rows()
    assert rows[0]["validation_net_bps"] is None
    assert rows[0]["N_required"] is None
    assert rows[0]["freeze_timestamp"] is None


def test_render_markdown_produces_a_row_per_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "VALIDATION_REGISTRY", tmp_path / "validation_registry.yaml")
    monkeypatch.setattr(sb, "LIVE_REGISTRY", tmp_path / "live_alpha_registry.yaml")
    monkeypatch.setattr(sb, "LAB_DIR", tmp_path / "lab")
    (tmp_path / "validation_registry.yaml").write_text("""
candidates:
  - candidate_id: A
    family: liquidation
    current_status: CANDIDATE
  - candidate_id: B
    family: cross_sectional
    current_status: CANDIDATE
""")
    (tmp_path / "live_alpha_registry.yaml").write_text("alphas: []")
    rows = sb.build_rows()
    md = sb.render_markdown(rows)
    assert "A" in md and "B" in md
    assert md.count("\n|") >= 2   # header separator + au moins 2 lignes de données
