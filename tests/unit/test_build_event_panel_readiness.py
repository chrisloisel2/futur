"""
tests/unit/test_build_event_panel_readiness.py
─────────────────────────────────────────────────────────────────────────────
scripts/build_event_panel_readiness.py: reports/EVENT_PANEL_READINESS.json
generation (mission section 14). Covers: gate detection against real
on-disk panel files (duplicate_pk, causality, irregular grid, PIT bounds,
warmup), EVENT_PANEL_READY never trivially True on zero rows, and that the
construction-logic gates are wired to the dedicated test suites rather
than silently defaulted to pass.

Gate:
    python3 -m pytest tests/unit/test_build_event_panel_readiness.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import build_event_panel_readiness as bpr


def _panel_rows(idx: pd.DatetimeIndex, symbol: str, **overrides) -> pd.DataFrame:
    n = len(idx)
    df = pd.DataFrame({
        "timestamp": idx, "research_available_at": idx + pd.Timedelta(seconds=305),
        "symbol": symbol, "open": np.full(n, 100.0), "close": np.full(n, 100.0),
        "volume": np.full(n, 10.0), "oi": np.full(n, 500.0), "oi_delta_pct_1h": np.full(n, 0.0),
        "aggressive_buy_usd": np.full(n, 1000.0), "aggressive_sell_usd": np.full(n, 900.0),
        "signed_volume": np.full(n, 100.0), "CVD": np.cumsum(np.full(n, 100.0)),
        "funding_rate": np.full(n, 0.0001), "funding_is_settlement": False,
        "time_since_last_funding": pd.Timedelta(0),
        "basis": np.full(n, 0.001), "basis_z_1d": np.full(n, 0.0), "basis_z_7d": np.full(n, 0.0),
        "residual_logret_5m": np.full(n, 0.0), "residual_return_15m": np.full(n, 0.0),
        "residual_return_1h": np.full(n, np.nan), "liq_feed_available": False,
    })
    for k, v in overrides.items():
        df[k] = v
    return df


def _write_panel(base: Path, symbol: str, df: pd.DataFrame) -> None:
    for y, chunk in df.groupby(df["timestamp"].dt.year):
        d = base / f"symbol={symbol}" / f"year={y}"
        d.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(d / "event_feature_panel_5m.parquet", index=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bpr, "PANEL_DIR", tmp_path / "panel")
    im = pd.DataFrame({
        "symbol": ["FOOUSDT"], "listing_ts": [pd.Timestamp("2024-01-01", tz="UTC")],
        "delisting_ts": [pd.NaT],
    })
    im_path = tmp_path / "instrument_master.parquet"
    im.to_parquet(im_path, index=False)
    monkeypatch.setattr(bpr, "INSTRUMENT_MASTER", im_path)
    monkeypatch.setattr(bpr, "_run_pytest", lambda *paths: True)
    return tmp_path


def test_empty_panel_never_ready(env):
    report = bpr.build()
    assert report["row_count"] == 0
    assert report["EVENT_PANEL_READY"] is False
    assert report["hard_gates"]["row_count_positive"] is False


def test_clean_panel_is_ready(env):
    idx = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
    _write_panel(env / "panel", "FOOUSDT", _panel_rows(idx, "FOOUSDT"))
    report = bpr.build()
    assert report["row_count"] == 20
    assert report["hard_gates"]["duplicate_pk"] is True
    assert report["hard_gates"]["causality_violations"] is True
    assert report["hard_gates"]["irregular_grid_unexplained"] is True
    assert report["hard_gates"]["pit_violations"] is True
    assert report["EVENT_PANEL_READY"] is True


def test_duplicate_pk_detected(env):
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    df = _panel_rows(idx, "FOOUSDT")
    df = pd.concat([df, df.iloc[[3]]], ignore_index=True)  # inject a duplicate (symbol, timestamp)
    _write_panel(env / "panel", "FOOUSDT", df)
    report = bpr.build()
    assert report["gate_values"]["duplicate_pk"] > 0
    assert report["hard_gates"]["duplicate_pk"] is False
    assert report["EVENT_PANEL_READY"] is False


def test_causality_violation_detected(env):
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    df = _panel_rows(idx, "FOOUSDT")
    df.loc[5, "research_available_at"] = df.loc[5, "timestamp"] - pd.Timedelta(minutes=1)  # RA before its own bar
    _write_panel(env / "panel", "FOOUSDT", df)
    report = bpr.build()
    assert report["gate_values"]["causality_violations"] == 1
    assert report["hard_gates"]["causality_violations"] is False
    assert report["EVENT_PANEL_READY"] is False


def test_gap_row_with_old_carried_forward_funding_is_not_a_causality_violation(env):
    """Bug found 2026-08-14: a gap row (no real perp bar, close=NaN) that
    only carries a funding rate forward from weeks ago legitimately has
    research_available_at far BEFORE its own label timestamp -- that is
    the correct, causal representation of 'nothing new happened at this
    bar', not a leak. An earlier version of the check applied
    unconditionally and flagged 19,287 such rows across 5 real symbols."""
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    df = _panel_rows(idx, "FOOUSDT")
    df.loc[5, "close"] = float("nan")  # no real market event at this bar
    df.loc[5, "open"] = float("nan")
    df.loc[5, "research_available_at"] = df.loc[5, "timestamp"] - pd.Timedelta(days=30)  # old carried-forward funding
    _write_panel(env / "panel", "FOOUSDT", df)
    report = bpr.build()
    assert report["gate_values"]["causality_violations"] == 0
    assert report["hard_gates"]["causality_violations"] is True


def test_btc_and_eth_are_exempt_from_the_warmup_check(env):
    """Bug found 2026-08-14: BTCUSDT/ETHUSDT are the regression benchmark,
    not a regressed symbol -- compute_residual_returns gives them
    residual == raw return at every bar by design, no beta-fitting warmup
    ever applies to them. An earlier version of this check flagged
    31,080 such legitimate rows across exactly BTCUSDT/ETHUSDT."""
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    df = _panel_rows(idx, "BTCUSDT", residual_return_1h=0.001)  # populated from bar 0, by design
    _write_panel(env / "panel", "BTCUSDT", df)
    report = bpr.build()
    assert report["gate_values"]["invalid_warmup_rows"] == 0
    assert report["hard_gates"]["invalid_warmup_rows"] is True


def test_irregular_grid_detected(env):
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC").delete(5)  # a missing row means an unexplained gap here
    _write_panel(env / "panel", "FOOUSDT", _panel_rows(idx, "FOOUSDT"))
    report = bpr.build()
    assert report["gate_values"]["irregular_grid_unexplained"] > 0
    assert report["hard_gates"]["irregular_grid_unexplained"] is False


def test_pit_violation_detected_rows_before_listing(env):
    idx = pd.date_range("2023-01-01", periods=10, freq="5min", tz="UTC")  # a full year before listing_ts
    _write_panel(env / "panel", "FOOUSDT", _panel_rows(idx, "FOOUSDT"))
    report = bpr.build()
    assert report["gate_values"]["pit_violations"] > 0
    assert report["hard_gates"]["pit_violations"] is False


def test_construction_logic_gates_fail_when_tests_fail(env, monkeypatch):
    idx = pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC")
    _write_panel(env / "panel", "FOOUSDT", _panel_rows(idx, "FOOUSDT"))
    monkeypatch.setattr(bpr, "_run_pytest", lambda *paths: False)
    report = bpr.build()
    assert report["hard_gates"]["future_joins"] is False
    assert report["hard_gates"]["required_feature_silent_ffill"] is False
    assert report["hard_gates"]["label_future_leak"] is False
    assert report["EVENT_PANEL_READY"] is False


def test_schema_and_provenance_hashes_present(env):
    report = bpr.build()
    assert report["schema_hash"]
    assert isinstance(report["schema_hash"], str)
    assert len(report["schema_hash"]) == 64  # sha256 hex digest
