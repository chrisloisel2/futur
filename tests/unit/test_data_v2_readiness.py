"""
tests/unit/test_data_v2_readiness.py
─────────────────────────────────────────────────────────────────────────────
scripts/build_data_v2_readiness.py: excluding a confirmed-unavailable
period from the coverage denominator (2026-08-11) -- the fix for the real
bug where ADAUSDT/ZRXUSDT's OI coverage could never reach the 95% gate no
matter how complete the backfill was, because their first 456 days are
genuinely 404 at the source (confirmed via the OI backfiller's own
manifest), not merely unfetched.

Gate:
    python3 -m pytest tests/unit/test_data_v2_readiness.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import build_data_v2_readiness as readiness_mod

TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _im_row(symbol: str, listing_ts) -> pd.DataFrame:
    return pd.DataFrame([{"symbol": symbol, "listing_ts": listing_ts, "delisting_ts": pd.NaT}])


def test_confirmed_unavailable_gap_shifts_expected_start(monkeypatch):
    """The exact ADAUSDT/ZRXUSDT case: OI data confirmed-404 for the whole
    gap between listing_ts and the real first row -- expected_start must
    move to the real first row, not stay at listing_ts forever."""
    im = _im_row("ADAUSDT", TS("2020-01-31"))
    df = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    all_missing = {d.date().isoformat() for d in pd.date_range("2020-01-31", "2021-11-30", freq="1D", tz="UTC")}

    fake_spec = {
        "oi_vision_5m": dict(loader=lambda s: None, ts_col="create_time",
                              source_available_from=None, granularity="day",
                              missing_fn=lambda s: all_missing)
    }
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", fake_spec)

    result = readiness_mod._confirmed_unavailable_expected_start("oi_vision_5m", "ADAUSDT", im, df, "create_time")
    assert result == df["create_time"].min()


def test_no_adjustment_when_gap_not_confirmed_unavailable(monkeypatch):
    im = _im_row("FOOUSDT", TS("2020-01-31"))
    df = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    fake_spec = {
        "oi_vision_5m": dict(loader=lambda s: None, ts_col="create_time",
                              source_available_from=None, granularity="day",
                              missing_fn=lambda s: set())  # nothing confirmed missing
    }
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", fake_spec)

    result = readiness_mod._confirmed_unavailable_expected_start("oi_vision_5m", "FOOUSDT", im, df, "create_time")
    assert result is None


def test_no_adjustment_when_no_gap_at_all(monkeypatch):
    im = _im_row("FOOUSDT", TS("2021-12-01"))
    df = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    fake_spec = {
        "oi_vision_5m": dict(loader=lambda s: None, ts_col="create_time",
                              source_available_from=None, granularity="day",
                              missing_fn=lambda s: {"2020-01-01"})
    }
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", fake_spec)

    result = readiness_mod._confirmed_unavailable_expected_start("oi_vision_5m", "FOOUSDT", im, df, "create_time")
    assert result is None


def test_agg_trades_flow_1m_reuses_5m_manifest_spec(monkeypatch):
    """1m and 5m share one manifest (the 1m builder's) -- evaluating
    agg_trades_flow_1m must look up the agg_trades_flow_5m spec key."""
    im = _im_row("FOOUSDT", TS("2020-01-01"))
    df = pd.DataFrame({"timestamp": pd.date_range("2021-01-01", periods=5, freq="5min", tz="UTC")})
    all_missing = {d.date().isoformat() for d in pd.date_range("2020-01-01", "2020-12-31", freq="1D", tz="UTC")}
    fake_spec = {
        "agg_trades_flow_5m": dict(loader=lambda s: None, ts_col="timestamp",
                                    source_available_from=None, granularity="day",
                                    missing_fn=lambda s: all_missing)
    }
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", fake_spec)

    result = readiness_mod._confirmed_unavailable_expected_start("agg_trades_flow_1m", "FOOUSDT", im, df, "timestamp")
    assert result == df["timestamp"].min()


def test_funding_has_no_manifest_spec_so_never_adjusted(monkeypatch):
    im = _im_row("FOOUSDT", TS("2020-01-01"))
    df = pd.DataFrame({"timestamp": pd.date_range("2021-01-01", periods=5, freq="8h", tz="UTC")})
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", {})  # funding not in DATASET_MANIFEST_SPECS

    result = readiness_mod._confirmed_unavailable_expected_start("funding", "FOOUSDT", im, df, "timestamp")
    assert result is None


def test_unresolved_listing_ts_returns_none(monkeypatch):
    im = _im_row("FOOUSDT", pd.NaT)
    df = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    fake_spec = {
        "oi_vision_5m": dict(loader=lambda s: None, ts_col="create_time",
                              source_available_from=None, granularity="day",
                              missing_fn=lambda s: {"2020-01-01"})
    }
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", fake_spec)

    result = readiness_mod._confirmed_unavailable_expected_start("oi_vision_5m", "FOOUSDT", im, df, "create_time")
    assert result is None
