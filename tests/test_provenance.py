"""
tests/test_provenance.py — item P1 (phase OPERATIONAL HARDENING) :
raw_event_id / feature_snapshot_id stamping for new decisions. Never
backfill old rows with a fabricated ID (trade_trace.py handles that side
by only reading these columns when present).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.institutional.live_alpha_lab.provenance import stamp_event_ids


def _df():
    return pd.DataFrame([
        {"event_time": pd.Timestamp("2026-09-01T00:00:00Z"), "symbol": "BTCUSDT", "score": 0.5},
        {"event_time": pd.Timestamp("2026-09-01T00:05:00Z"), "symbol": "ETHUSDT", "score": 0.7},
    ])


def test_adds_both_id_columns():
    out = stamp_event_ids(_df(), "TEST_ALPHA", "event_time", "symbol")
    assert "raw_event_id" in out.columns
    assert "feature_snapshot_id" in out.columns
    assert out["raw_event_id"].notna().all()
    assert out["feature_snapshot_id"].notna().all()


def test_raw_event_id_deterministic_for_same_alpha_symbol_time():
    out1 = stamp_event_ids(_df(), "TEST_ALPHA", "event_time", "symbol")
    out2 = stamp_event_ids(_df(), "TEST_ALPHA", "event_time", "symbol")
    assert (out1["raw_event_id"] == out2["raw_event_id"]).all()


def test_raw_event_id_differs_across_rows_with_different_symbol_or_time():
    out = stamp_event_ids(_df(), "TEST_ALPHA", "event_time", "symbol")
    assert out["raw_event_id"].iloc[0] != out["raw_event_id"].iloc[1]


def test_raw_event_id_differs_by_alpha_id_same_symbol_time():
    """Le même événement marché (symbole+timestamp) traité par deux alphas
    différents doit produire des raw_event_id DIFFÉRENTS -- ce n'est pas le
    même 'événement de décision', même si le marché sous-jacent est le même
    instant."""
    df = _df().iloc[[0]]
    out_a = stamp_event_ids(df, "ALPHA_A", "event_time", "symbol")
    out_b = stamp_event_ids(df, "ALPHA_B", "event_time", "symbol")
    assert out_a["raw_event_id"].iloc[0] != out_b["raw_event_id"].iloc[0]


def test_feature_snapshot_id_changes_when_feature_values_change():
    df1 = _df()
    df2 = _df()
    df2.loc[0, "score"] = 0.99   # une feature différente -> empreinte différente
    out1 = stamp_event_ids(df1, "TEST_ALPHA", "event_time", "symbol")
    out2 = stamp_event_ids(df2, "TEST_ALPHA", "event_time", "symbol")
    assert out1["feature_snapshot_id"].iloc[0] != out2["feature_snapshot_id"].iloc[0]


def test_feature_snapshot_id_same_for_identical_feature_content():
    out1 = stamp_event_ids(_df(), "TEST_ALPHA", "event_time", "symbol")
    out2 = stamp_event_ids(_df(), "TEST_ALPHA", "event_time", "symbol")
    assert (out1["feature_snapshot_id"] == out2["feature_snapshot_id"]).all()


def test_symbol_col_none_uses_explicit_market_wide_sentinel_not_a_crash():
    """VOL_FORECAST_LAYER_V1 n'a pas de colonne symbole par ligne (panel
    market-wide) -- ne doit ni planter ni fabriquer un symbole inventé."""
    df = pd.DataFrame([{"event_time": pd.Timestamp("2026-09-01T00:00:00Z"), "combined_forecast_z": 1.2}])
    out = stamp_event_ids(df, "VOL_FORECAST_LAYER_V1", "event_time", symbol_col=None)
    assert out["raw_event_id"].notna().all()
    # même event_time, alpha_id différent -> raw_event_id différent malgré symbol_col=None des deux côtés
    out_other = stamp_event_ids(df, "OTHER_ALPHA", "event_time", symbol_col=None)
    assert out["raw_event_id"].iloc[0] != out_other["raw_event_id"].iloc[0]


def test_does_not_mutate_input_dataframe():
    df = _df()
    original_cols = list(df.columns)
    stamp_event_ids(df, "TEST_ALPHA", "event_time", "symbol")
    assert list(df.columns) == original_cols
