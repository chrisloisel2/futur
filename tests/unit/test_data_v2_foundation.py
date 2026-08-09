"""
tests/unit/test_data_v2_foundation.py
─────────────────────────────────────────────────────────────────────────────
Data V2 steps 3/10/11/12: InstrumentMaster, coverage validator, discrete
funding events, canonical available_at. Uses real on-disk data
(instrument_master.parquet, BTCUSDT funding) where available, synthetic
fixtures otherwise.

Gate:
    python3 -m pytest tests/unit/test_data_v2_foundation.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.validation.validator import validate_series
from data_v2.features.funding_events import (
    crosses_settlement,
    funding_cost_for_window,
    round_to_settlement_hour,
    settlements_between,
)
from data_v2.temporal.available_at import add_temporal_columns, assert_causal

ROOT = Path(__file__).resolve().parents[2]
INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
BTC_FUNDING = ROOT / "data/derivatives_backfill/binance/funding/BTCUSDT.parquet"


# ── InstrumentMaster ───────────────────────────────────────────────────────


@pytest.mark.skipif(not INSTRUMENT_MASTER.exists(), reason="instrument_master.parquet not built yet")
def test_instrument_master_covers_pit_universe_with_no_gaps():
    im = pd.read_parquet(INSTRUMENT_MASTER)
    assert len(im) >= 300
    assert im["symbol"].is_unique
    assert im["listing_ts"].notna().all()
    # every currently-live row must carry real filter values, not fabricated
    live = im[im["source"] == "exchange_info_live"]
    assert live["tick_size"].notna().all()
    assert live["step_size"].notna().all()


@pytest.mark.skipif(not INSTRUMENT_MASTER.exists(), reason="instrument_master.parquet not built yet")
def test_instrument_master_btc_listing_matches_known_launch():
    im = pd.read_parquet(INSTRUMENT_MASTER)
    btc = im.loc[im["symbol"] == "BTCUSDT"].iloc[0]
    assert btc["listing_ts"].date().isoformat() == "2019-09-08"
    assert pd.isna(btc["delisting_ts"])  # still trading


# ── validator ────────────────────────────────────────────────────────────


def _make_5m_series(n_days: int, gap_at: int | None = None) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n_days * 288, freq="5min", tz="UTC")
    if gap_at is not None:
        idx = idx.delete(range(gap_at, gap_at + 20))  # drop ~100 minutes
    return pd.DataFrame({"create_time": idx, "sum_open_interest": np.random.default_rng(0).uniform(1, 100, len(idx))})


def test_validator_full_coverage_passes():
    df = _make_5m_series(10)
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300)
    assert report.coverage_pct > 0.99
    assert report.gap_count == 0
    assert report.passed


def test_validator_detects_gap():
    df = _make_5m_series(10, gap_at=500)
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300)
    assert report.gap_count >= 1
    assert report.coverage_pct < 1.0


def test_validator_detects_duplicate_pk():
    df = _make_5m_series(3)
    dup = pd.concat([df, df.iloc[:5]], ignore_index=True)
    report = validate_series(dup, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300, pk_cols=["create_time"])
    assert report.duplicate_pk == 10  # 5 rows x 2 occurrences each
    assert not report.passed


def test_validator_detects_corruption_non_positive_required_column():
    df = _make_5m_series(3)
    df.loc[df.index[0], "sum_open_interest"] = -5.0
    report = validate_series(
        df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
        required_positive_columns=["sum_open_interest"],
    )
    assert report.corruption >= 1
    assert not report.passed


def test_validator_flags_rows_before_listing():
    df = _make_5m_series(5)  # starts 2024-01-01
    im = pd.DataFrame([{"symbol": "BTCUSDT", "listing_ts": pd.Timestamp("2024-01-03", tz="UTC"), "delisting_ts": pd.NaT}])
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300, instrument_master=im)
    assert report.listing_alignment == "rows_before_listing"
    assert not report.passed


@pytest.mark.skipif(not (Path("data/derivatives_backfill/binance_vision_metrics/BTCUSDT_metrics_5m.parquet")).exists(),
                     reason="real OI vision data not present")
def test_validator_against_real_oi_data_reports_plausible_coverage():
    df = pd.read_parquet("data/derivatives_backfill/binance_vision_metrics/BTCUSDT_metrics_5m.parquet")
    report = validate_series(
        df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300, source="binance_vision_metrics_5m",
        required_positive_columns=["sum_open_interest"],
    )
    assert report.actual_rows > 100_000
    assert report.coverage_pct > 0.5  # real feed has known ~2985 gaps>5m per prior audit, not perfect


# ── funding events ──────────────────────────────────────────────────────


def test_round_to_settlement_hour_collapses_jitter():
    ts = pd.Series([pd.Timestamp("2021-01-01 00:00:00.002", tz="UTC"), pd.Timestamp("2021-01-01 08:00:00.006", tz="UTC")])
    rounded = round_to_settlement_hour(ts)
    assert rounded.iloc[0] == pd.Timestamp("2021-01-01 00:00:00", tz="UTC")
    assert rounded.iloc[1] == pd.Timestamp("2021-01-01 08:00:00", tz="UTC")


def test_crosses_settlement_true_and_false():
    assert crosses_settlement(pd.Timestamp("2024-01-01 07:00", tz="UTC"), pd.Timestamp("2024-01-01 09:00", tz="UTC"))
    assert not crosses_settlement(pd.Timestamp("2024-01-01 01:00", tz="UTC"), pd.Timestamp("2024-01-01 02:00", tz="UTC"))


def test_funding_cost_long_pays_positive_rate():
    funding_df = pd.DataFrame({
        "timestamp": [pd.Timestamp("2024-01-01 08:00", tz="UTC")],
        "funding_rate": [0.0005],
    })
    result = funding_cost_for_window(
        funding_df, pd.Timestamp("2024-01-01 07:00", tz="UTC"), pd.Timestamp("2024-01-01 09:00", tz="UTC"),
        position_notional_usd=10_000, side="long",
    )
    assert result.crossed_settlements == 1
    assert result.total_funding_paid_usd == pytest.approx(-5.0)  # long pays 0.0005 * 10000


def test_funding_cost_short_receives_positive_rate():
    funding_df = pd.DataFrame({
        "timestamp": [pd.Timestamp("2024-01-01 08:00", tz="UTC")],
        "funding_rate": [0.0005],
    })
    result = funding_cost_for_window(
        funding_df, pd.Timestamp("2024-01-01 07:00", tz="UTC"), pd.Timestamp("2024-01-01 09:00", tz="UTC"),
        position_notional_usd=10_000, side="short",
    )
    assert result.total_funding_paid_usd == pytest.approx(5.0)


def test_funding_cost_no_crossing_is_zero_not_prorated():
    funding_df = pd.DataFrame({
        "timestamp": [pd.Timestamp("2024-01-01 08:00", tz="UTC")],
        "funding_rate": [0.0005],
    })
    result = funding_cost_for_window(
        funding_df, pd.Timestamp("2024-01-01 08:30", tz="UTC"), pd.Timestamp("2024-01-01 08:45", tz="UTC"),
        position_notional_usd=10_000, side="long",
    )
    assert result.crossed_settlements == 0
    assert result.total_funding_paid_usd == 0.0


@pytest.mark.skipif(not BTC_FUNDING.exists(), reason="real funding data not present")
def test_funding_real_btc_data_settles_every_8h():
    df = pd.read_parquet(BTC_FUNDING)
    diffs = pd.to_datetime(df["timestamp"], utc=True).diff().dropna()
    modal_hours = diffs.dt.total_seconds().div(3600).round().mode()
    assert 8.0 in modal_hours.values


# ── available_at ────────────────────────────────────────────────────────


def test_add_temporal_columns_batch_source_lags_event_time():
    df = pd.DataFrame({"open_time": [pd.Timestamp("2024-06-15", tz="UTC")]})
    out = add_temporal_columns(df, event_time_col="open_time", source_kind="binance_vision_daily")
    assert out["available_at"].iloc[0] > out["event_time"].iloc[0]


def test_add_temporal_columns_live_stream_uses_recv_time():
    df = pd.DataFrame({
        "timestamp": [pd.Timestamp("2024-06-15 00:00:00", tz="UTC")],
        "recv_time": [pd.Timestamp("2024-06-15 00:00:00.150", tz="UTC")],
    })
    out = add_temporal_columns(df, event_time_col="timestamp", source_kind="live_stream", received_at_col="recv_time")
    assert out["received_at"].iloc[0] == pd.Timestamp("2024-06-15 00:00:00.150", tz="UTC")
    assert out["available_at"].iloc[0] > out["received_at"].iloc[0]


def test_assert_causal_raises_on_leakage():
    df = pd.DataFrame({
        "feature_ts": [pd.Timestamp("2024-01-01 00:00", tz="UTC")],
        "available_at": [pd.Timestamp("2024-01-02 00:00", tz="UTC")],  # available AFTER the feature ts: leakage
    })
    with pytest.raises(ValueError):
        assert_causal(df, as_of_col="feature_ts")


def test_assert_causal_passes_when_available_before_as_of():
    df = pd.DataFrame({
        "feature_ts": [pd.Timestamp("2024-01-02 00:00", tz="UTC")],
        "available_at": [pd.Timestamp("2024-01-01 00:00", tz="UTC")],
    })
    assert_causal(df, as_of_col="feature_ts")  # must not raise
