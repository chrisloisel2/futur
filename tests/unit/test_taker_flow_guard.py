"""
tests/unit/test_taker_flow_guard.py
─────────────────────────────────────────────────────────────────────────────
Blacklist regression test for the data/enriched taker_buy_* placeholder
(taker_buy_base_asset_volume == volume * 0.5, taker_buy_quote_asset_volume
== quote_asset_volume * 0.5 on ~every row — see memory
data_pitfalls_enriched_vision). No loader or feature builder may treat this
fabricated 50/50 split as real aggressor flow.

Gate:
    python3 -m pytest tests/unit/test_taker_flow_guard.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_pipeline.normalization import standardize_ohlcv_columns
from data_pipeline.enriched_ohlcv_features import compute_enriched_ohlcv_features
from data_pipeline.taker_flow_guard import (
    PlaceholderTakerFlowError,
    assert_no_placeholder_taker_flow,
    looks_like_placeholder_taker_flow,
)
from core.features.minute import compute_features_1m, compute_multitf_context
from ai.level_0.feature_engineering import compute_flow_features


def _base_ohlcv(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(0, 0.1, n))
    volume = rng.uniform(10, 100, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": volume,
            "quote_asset_volume": close * volume,
            "number_of_trades": rng.integers(10, 200, n),
        },
        index=idx,
    )


def _placeholder_ohlcv(n: int = 300) -> pd.DataFrame:
    df = _base_ohlcv(n)
    df["taker_buy_base_asset_volume"] = df["volume"] * 0.5
    df["taker_buy_quote_asset_volume"] = df["quote_asset_volume"] * 0.5
    return df


def _genuine_ohlcv(n: int = 300) -> pd.DataFrame:
    df = _base_ohlcv(n)
    rng = np.random.default_rng(1)
    ratio = rng.uniform(0.2, 0.8, n)
    df["taker_buy_base_asset_volume"] = df["volume"] * ratio
    df["taker_buy_quote_asset_volume"] = df["quote_asset_volume"] * ratio
    return df


# ── Detector ─────────────────────────────────────────────────────────────


def test_detector_flags_placeholder_50_50_split():
    assert looks_like_placeholder_taker_flow(_placeholder_ohlcv()) is True


def test_detector_does_not_flag_genuine_flow():
    assert looks_like_placeholder_taker_flow(_genuine_ohlcv()) is False


def test_detector_does_not_flag_missing_columns():
    assert looks_like_placeholder_taker_flow(_base_ohlcv()) is False


def test_assert_raises_on_placeholder():
    with pytest.raises(PlaceholderTakerFlowError):
        assert_no_placeholder_taker_flow(_placeholder_ohlcv())


def test_assert_passes_on_genuine_flow():
    assert_no_placeholder_taker_flow(_genuine_ohlcv())  # must not raise


# ── standardize_ohlcv_columns no longer fabricates the placeholder ───────


def test_standardize_ohlcv_columns_does_not_synthesize_taker_flow():
    raw = _base_ohlcv().drop(columns=["quote_asset_volume"]).reset_index(names="timestamp")
    out = standardize_ohlcv_columns(raw)
    assert "taker_buy_base_asset_volume" not in out.columns
    assert "taker_buy_quote_asset_volume" not in out.columns


# ── Feature builders must reject the placeholder ──────────────────────────


def test_enriched_ohlcv_features_rejects_placeholder():
    with pytest.raises(PlaceholderTakerFlowError):
        compute_enriched_ohlcv_features(_placeholder_ohlcv(), include_labels=False)


def test_enriched_ohlcv_features_accepts_genuine_flow_and_emits_ratio():
    out = compute_enriched_ohlcv_features(_genuine_ohlcv(), include_labels=False)
    assert "taker_buy_ratio_base" in out.columns
    assert not np.isclose(out["taker_buy_ratio_base"].dropna(), 0.5).all()


def test_enriched_ohlcv_features_omits_taker_columns_when_absent():
    out = compute_enriched_ohlcv_features(_base_ohlcv(), include_labels=False)
    assert "taker_buy_ratio_base" not in out.columns
    assert "taker_buy_base" not in out.columns


def test_minute_features_reject_placeholder():
    with pytest.raises(PlaceholderTakerFlowError):
        compute_features_1m(_placeholder_ohlcv())


def test_minute_features_accept_genuine_flow():
    out = compute_features_1m(_genuine_ohlcv())
    assert "taker_delta" in out.columns


def test_multitf_context_rejects_placeholder():
    with pytest.raises(PlaceholderTakerFlowError):
        compute_multitf_context(_placeholder_ohlcv())


def test_compute_flow_features_rejects_placeholder():
    with pytest.raises(PlaceholderTakerFlowError):
        compute_flow_features(_placeholder_ohlcv())


def test_compute_flow_features_accepts_genuine_flow_and_is_not_flat():
    out = compute_flow_features(_genuine_ohlcv())
    assert out["volume_delta"].std() > 0
