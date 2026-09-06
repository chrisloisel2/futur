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
    # (InstrumentMaster V2: "source" was replaced by exchangeinfo_status,
    # which carries the same "currently TRADING" signal plus every other
    # live exchangeInfo status, e.g. SETTLING -- see build_instrument_master.py)
    live = im[im["exchangeinfo_status"] == "TRADING"]
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


def _fresh_now(df: pd.DataFrame, col: str = "create_time") -> pd.Timestamp:
    """now= pinned just 1 day after the series' own last row -- avoids
    tripping the (now-blocking) staleness gate in tests that aren't about
    staleness, regardless of the series' own length."""
    return df[col].max() + pd.Timedelta(days=1)


def test_validator_full_coverage_passes():
    df = _make_5m_series(10)
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300, now=_fresh_now(df))
    assert report.coverage_pct > 0.99
    assert report.gap_count == 0
    assert report.passed


def test_validator_detects_gap():
    df = _make_5m_series(10, gap_at=500)
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300, now=_fresh_now(df))
    assert report.gap_count >= 1
    assert report.coverage_pct < 1.0


def test_validator_detects_duplicate_pk():
    df = _make_5m_series(3)
    dup = pd.concat([df, df.iloc[:5]], ignore_index=True)
    report = validate_series(dup, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              pk_cols=["create_time"], now=_fresh_now(df))
    assert report.duplicate_pk == 10  # 5 rows x 2 occurrences each
    assert not report.passed


def test_validator_detects_corruption_non_positive_required_column():
    df = _make_5m_series(3)
    df.loc[df.index[0], "sum_open_interest"] = -5.0
    report = validate_series(
        df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
        required_positive_columns=["sum_open_interest"], now=_fresh_now(df),
    )
    assert report.corruption >= 1
    assert not report.passed


def test_validator_required_nonnegative_allows_exact_zero_but_not_negative():
    # a newly-listed thin OI contract can genuinely show sum_open_interest
    # == 0.0 for a few bars -- that must NOT count as corruption (required_
    # positive_columns would wrongly flag it; required_nonnegative_columns
    # is for exactly this case: only a negative value is truly impossible).
    df = _make_5m_series(3)
    df.loc[df.index[0], "sum_open_interest"] = 0.0
    report = validate_series(
        df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
        required_nonnegative_columns=["sum_open_interest"], now=_fresh_now(df),
    )
    assert report.corruption == 0

    df.loc[df.index[1], "sum_open_interest"] = -1.0
    report2 = validate_series(
        df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
        required_nonnegative_columns=["sum_open_interest"], now=_fresh_now(df),
    )
    assert report2.corruption == 1


def test_validator_flags_rows_before_listing():
    df = _make_5m_series(5)  # starts 2024-01-01
    im = pd.DataFrame([{"symbol": "BTCUSDT", "listing_ts": pd.Timestamp("2024-01-03", tz="UTC"), "delisting_ts": pd.NaT}])
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=_fresh_now(df))
    assert report.listing_alignment == "rows_before_listing"
    assert not report.passed


def test_validator_listing_alignment_grace_days_widens_tolerance_for_month_cadence():
    """The spot_5m/perp_5m case: a month-granularity archive fetched
    "the month containing the true listing timestamp" genuinely includes
    real days from earlier in that month -- e.g. listed 2024-01-28 but the
    fetched archive starts 2024-01-01. With the default (~1 day) grace this
    is a PIT violation; with a month-scale grace (e.g. 31 days) it's
    correctly recognized as real, benign pre-listing-month data."""
    idx = pd.date_range("2024-01-01", periods=5 * 288, freq="5min", tz="UTC")
    df = pd.DataFrame({"create_time": idx, "sum_open_interest": 1.0})
    im = pd.DataFrame([{"symbol": "BTCUSDT", "listing_ts": pd.Timestamp("2024-01-28", tz="UTC"), "delisting_ts": pd.NaT}])

    default_report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                                      instrument_master=im, now=_fresh_now(df))
    assert default_report.listing_alignment == "rows_before_listing"

    widened_report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                                      instrument_master=im, now=_fresh_now(df), listing_alignment_grace_days=31.0)
    assert widened_report.listing_alignment == "ok"


def test_validator_listing_alignment_grace_days_still_catches_a_real_violation():
    """A genuine multi-month PIT mismatch (e.g. a ticker rename, or data
    continuing long past a real delisting) must stay flagged even at the
    widened month-cadence grace -- the grace absorbs "one archive month
    of slack", not an unbounded amount."""
    idx = pd.date_range("2024-01-01", periods=5 * 288, freq="5min", tz="UTC")
    df = pd.DataFrame({"create_time": idx, "sum_open_interest": 1.0})
    im = pd.DataFrame([{"symbol": "BTCUSDT", "listing_ts": pd.Timestamp("2024-06-01", tz="UTC"), "delisting_ts": pd.NaT}])

    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=_fresh_now(df), listing_alignment_grace_days=31.0)
    assert report.listing_alignment == "rows_before_listing"


def test_validator_blocking_staleness_fails_gate_for_active_symbol():
    df = _make_5m_series(5)  # last row 2024-01-06-ish
    im = pd.DataFrame([{"symbol": "BTCUSDT", "listing_ts": pd.Timestamp("2023-01-01", tz="UTC"), "delisting_ts": pd.NaT}])
    far_future_now = pd.Timestamp("2024-06-01", tz="UTC")  # ~5 months after last row, symbol still "active"
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=far_future_now, staleness_gate_days=3.0)
    assert report.staleness_gate_violated
    assert not report.passed


def test_validator_delisted_symbol_is_not_penalized_for_staleness():
    df = _make_5m_series(5)
    delist_ts = df["create_time"].max()
    im = pd.DataFrame([{"symbol": "OLDUSDT", "listing_ts": pd.Timestamp("2023-01-01", tz="UTC"), "delisting_ts": delist_ts}])
    far_future_now = pd.Timestamp("2026-01-01", tz="UTC")
    report = validate_series(df, symbol="OLDUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=far_future_now)
    assert not report.staleness_gate_violated  # delisted -- no data after delisting is expected, not "stale"


def test_validator_staleness_measured_against_explicit_expected_end_not_now():
    """Bug found 2026-08-14 (external review): staleness used to be
    `now - window_end` unconditionally for a non-delisted symbol, ignoring
    any explicit `expected_end` the caller passed in (e.g. a confirmed-
    unavailable trailing bound, or a monthly publication watermark). A
    symbol whose data is proven complete up to its own real theoretical
    bound must never be flagged stale just because `now` has since moved
    far past that bound -- the exact AGIXUSDT spot case (confirmed
    stopped 2024-07, `now` in 2026-08)."""
    df = _make_5m_series(5)
    window_end = df["create_time"].max()
    im = pd.DataFrame([{"symbol": "AGIXUSDT", "listing_ts": pd.Timestamp("2023-01-01", tz="UTC"), "delisting_ts": pd.NaT}])
    far_future_now = pd.Timestamp("2026-01-01", tz="UTC")  # ~2 years after window_end
    report = validate_series(
        df, symbol="AGIXUSDT", timestamp_col="create_time", bar_seconds=300,
        instrument_master=im, now=far_future_now, staleness_gate_days=3.0,
        expected_end=window_end,  # e.g. a confirmed-unavailable trailing bound
    )
    assert not report.staleness_gate_violated
    assert report.staleness == pd.Timedelta(0)


def test_validator_staleness_still_catches_a_genuine_gap_against_expected_end():
    """The explicit-expected_end path must still catch a REAL gap: data
    that falls meaningfully short of even the (non-`now`) theoretical
    bound the caller passed in."""
    df = _make_5m_series(5)
    im = pd.DataFrame([{"symbol": "AGIXUSDT", "listing_ts": pd.Timestamp("2023-01-01", tz="UTC"), "delisting_ts": pd.NaT}])
    far_future_now = pd.Timestamp("2026-01-01", tz="UTC")
    genuinely_later_bound = df["create_time"].max() + pd.Timedelta(days=30)
    report = validate_series(
        df, symbol="AGIXUSDT", timestamp_col="create_time", bar_seconds=300,
        instrument_master=im, now=far_future_now, staleness_gate_days=3.0,
        expected_end=genuinely_later_bound,
    )
    assert report.staleness_gate_violated
    assert report.staleness == pd.Timedelta(days=30)


def test_validator_fails_on_short_recent_slice_of_long_expected_history():
    """The literal scenario this fix targets: a registry could show
    status=PASS for e.g. an OI REST file with ~500 rows spanning 2026-07-01
    to 2026-07-22 while the symbol has genuinely existed (and Vision data
    should exist) for 3 years -- PASS meant "valid acquisition", not
    "sufficient history to search for alpha in". coverage_pct must reflect
    the full expected span (listing_ts -> now), not the data's own window.
    """
    idx = pd.date_range("2026-07-01", periods=500, freq="1h", tz="UTC")
    df = pd.DataFrame({"create_time": idx, "sum_open_interest": 1.0})
    im = pd.DataFrame([{"symbol": "XUSDT", "listing_ts": pd.Timestamp("2023-07-01", tz="UTC"), "delisting_ts": pd.NaT}])
    now = pd.Timestamp("2026-07-22", tz="UTC")

    report = validate_series(df, symbol="XUSDT", timestamp_col="create_time", bar_seconds=3600,
                              instrument_master=im, now=now)
    assert report.expected_span_known
    assert report.coverage_pct < 0.05  # 500 rows vs ~3 years hourly (~26280 expected)
    assert not report.passed


def test_validator_confirmed_unavailable_days_excluded_from_coverage_even_when_scattered():
    """Bug found 2026-08-14 (user-authorized fix): the pre-existing
    confirmed-unavailable handling only ever excluded a single contiguous
    boundary run. A symbol with real, scattered, individually-confirmed
    404 days spread throughout its history (the real ALPHAUSDT pattern,
    574/2089 days) could never reach a high coverage_pct no matter how
    exhaustively backfilled, because those scattered days permanently
    counted against the denominator. confirmed_unavailable_periods must
    exclude them regardless of position in the window."""
    idx = pd.date_range("2024-01-01", periods=10 * 288, freq="5min", tz="UTC")  # 10 days, full coverage
    df = pd.DataFrame({"create_time": idx, "sum_open_interest": 1.0})
    im = pd.DataFrame([{"symbol": "XUSDT", "listing_ts": pd.Timestamp("2024-01-01", tz="UTC"), "delisting_ts": pd.NaT}])
    now = pd.Timestamp("2024-01-21", tz="UTC")  # expected window is 20 days, only 10 have real data

    # without the fix: 10 real days / 20 expected days = 50% coverage
    baseline = validate_series(df, symbol="XUSDT", timestamp_col="create_time", bar_seconds=300,
                                instrument_master=im, now=now, expected_end=now)
    assert baseline.coverage_pct == pytest.approx(0.5, abs=0.01)

    # the missing 10 days are scattered confirmed-404s (not contiguous,
    # not a simple boundary run) -- must be fully excluded from the denominator
    confirmed_missing = {
        (pd.Timestamp("2024-01-01") + pd.Timedelta(days=d)).date().isoformat()
        for d in range(10, 20)
    }
    fixed = validate_series(df, symbol="XUSDT", timestamp_col="create_time", bar_seconds=300,
                             instrument_master=im, now=now, expected_end=now,
                             confirmed_unavailable_periods=confirmed_missing,
                             confirmed_unavailable_granularity="day")
    assert fixed.coverage_pct == pytest.approx(1.0, abs=0.01)


def test_validator_confirmed_unavailable_days_do_not_over_exclude_real_gaps():
    """Only genuinely confirmed-unavailable periods are excluded -- an
    UN-confirmed gap (not in confirmed_unavailable_periods) must still
    count against coverage, even if it looks similar."""
    idx = pd.date_range("2024-01-01", periods=10 * 288, freq="5min", tz="UTC")
    df = pd.DataFrame({"create_time": idx, "sum_open_interest": 1.0})
    im = pd.DataFrame([{"symbol": "XUSDT", "listing_ts": pd.Timestamp("2024-01-01", tz="UTC"), "delisting_ts": pd.NaT}])
    now = pd.Timestamp("2024-01-21", tz="UTC")

    report = validate_series(df, symbol="XUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=now, expected_end=now,
                              confirmed_unavailable_periods=set(),  # nothing confirmed -- e.g. never attempted
                              confirmed_unavailable_granularity="day")
    assert report.coverage_pct == pytest.approx(0.5, abs=0.01)  # unchanged -- still a real, uncounted gap


def test_validator_confirmed_unavailable_months_excluded_for_month_granularity():
    idx = pd.date_range("2024-01-01", periods=31 * 288, freq="5min", tz="UTC")  # full January, real data
    df = pd.DataFrame({"create_time": idx, "sum_open_interest": 1.0})
    im = pd.DataFrame([{"symbol": "XUSDT", "listing_ts": pd.Timestamp("2024-01-01", tz="UTC"), "delisting_ts": pd.NaT}])
    now = pd.Timestamp("2024-03-01", tz="UTC")  # expected span covers Jan+Feb, but no data past January

    confirmed_missing_months = {"2024-02"}  # February confirmed unavailable at the source
    report = validate_series(df, symbol="XUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=now, expected_end=now,
                              confirmed_unavailable_periods=confirmed_missing_months,
                              confirmed_unavailable_granularity="month")
    # expected span becomes ~just January -- fully covered by real data
    assert report.coverage_pct > 0.95


def test_validator_strict_mode_fails_when_expected_span_unknown_even_if_coverage_looks_fine():
    """Without an instrument_master row (or explicit expected_start/end),
    coverage_pct falls back to the data's own observed span -- internally
    consistent (no gaps), but meaningless as a "is history complete" signal.
    strict_alpha_readiness must not let that count toward DATA_V2_READY."""
    df = _make_5m_series(10)  # perfect internal coverage, no external expectation given
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              now=_fresh_now(df), strict_alpha_readiness=True)
    assert not report.expected_span_known
    assert report.coverage_pct > 0.99  # looks fine in isolation...
    assert not report.passed  # ...but strict mode must still fail it


def test_validator_strict_mode_passes_when_expected_span_known_and_coverage_is_real():
    df = _make_5m_series(10)
    im = pd.DataFrame([{"symbol": "BTCUSDT", "listing_ts": df["create_time"].min(), "delisting_ts": pd.NaT}])
    # now pinned right at the last bar (not _fresh_now's +1 day) -- this
    # test is about strict_alpha_readiness passing on genuinely complete
    # coverage, not about exercising the (separate, correct) staleness gap
    # that "now" being later than the last row would legitimately create.
    now = df["create_time"].max() + pd.Timedelta(minutes=1)
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              instrument_master=im, now=now, strict_alpha_readiness=True)
    assert report.expected_span_known
    assert report.passed


def test_validator_non_strict_mode_unaffected_by_unknown_span():
    df = _make_5m_series(10)
    report = validate_series(df, symbol="BTCUSDT", timestamp_col="create_time", bar_seconds=300,
                              now=_fresh_now(df))  # strict_alpha_readiness=False (default)
    assert not report.expected_span_known
    assert report.passed  # default mode: unknown span doesn't block a local/dev pass


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


# ── variable_cadence (funding, 2026-08-11) ─────────────────────────────


def test_validator_variable_cadence_dense_settlement_not_penalized():
    """The exact bug found on AIAUSDT: real funding settling every 1h/4h
    (Binance's dynamic funding interval) instead of the standard 8h used
    to report an impossible >100% coverage_pct under a fixed 3/day
    expectation. Under variable_cadence, a denser-than-8h real cadence
    must read as ~complete coverage, not be treated as "too many rows"."""
    idx = pd.date_range("2026-01-01", periods=30 * 24, freq="1h", tz="UTC")  # settles every hour, 30 days
    df = pd.DataFrame({"timestamp": idx, "funding_rate": 0.0001})
    im = pd.DataFrame([{"symbol": "DENSEUSDT", "listing_ts": idx[0], "delisting_ts": pd.NaT}])
    now = idx[-1] + pd.Timedelta(hours=1)

    report = validate_series(df, symbol="DENSEUSDT", timestamp_col="timestamp", bar_seconds=8 * 3600,
                              instrument_master=im, now=now, variable_cadence=True)
    assert report.coverage_pct == pytest.approx(1.0, abs=1e-6)
    assert report.passed


def test_validator_variable_cadence_still_catches_a_real_missed_settlement():
    """A genuine gap (no settlement for 20h, well past the 8h ceiling) must
    still show up as missing coverage under variable_cadence -- it isn't a
    blanket "never fails" mode, only "denser than expected never fails"."""
    idx = list(pd.date_range("2026-01-01", periods=10, freq="8h", tz="UTC"))
    del idx[5]  # remove one settlement -> a 16h gap where 8h was expected
    df = pd.DataFrame({"timestamp": idx, "funding_rate": 0.0001})
    im = pd.DataFrame([{"symbol": "GAPUSDT", "listing_ts": idx[0], "delisting_ts": pd.NaT}])
    now = idx[-1] + pd.Timedelta(hours=1)

    report = validate_series(df, symbol="GAPUSDT", timestamp_col="timestamp", bar_seconds=8 * 3600,
                              instrument_master=im, now=now, variable_cadence=True)
    assert report.coverage_pct < 0.98
    assert report.gap_count == 1


def test_validator_variable_cadence_standard_8h_symbol_unaffected():
    idx = pd.date_range("2026-01-01", periods=30, freq="8h", tz="UTC")
    df = pd.DataFrame({"timestamp": idx, "funding_rate": 0.0001})
    im = pd.DataFrame([{"symbol": "STDUSDT", "listing_ts": idx[0], "delisting_ts": pd.NaT}])
    now = idx[-1] + pd.Timedelta(hours=1)

    report = validate_series(df, symbol="STDUSDT", timestamp_col="timestamp", bar_seconds=8 * 3600,
                              instrument_master=im, now=now, variable_cadence=True)
    assert report.coverage_pct == pytest.approx(1.0, abs=1e-6)


@pytest.mark.skipif(not Path("data/derivatives_backfill/binance/funding/AIAUSDT.parquet").exists(),
                     reason="real AIAUSDT funding data not present")
def test_validator_variable_cadence_on_real_aiausdt_funding_no_longer_over_100pct():
    df = pd.read_parquet("data/derivatives_backfill/binance/funding/AIAUSDT.parquet")
    im = pd.read_parquet("data_v2/instruments/instrument_master.parquet")
    now = pd.Timestamp.now(tz="UTC")
    report = validate_series(df, symbol="AIAUSDT", timestamp_col="timestamp", bar_seconds=8 * 3600,
                              instrument_master=im, now=now, variable_cadence=True)
    assert report.coverage_pct <= 1.0 + 1e-6  # the real ~147% anomaly must be gone


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
# add_temporal_columns' own behavior (market vs archive vs research vs
# execution availability) is covered exhaustively in
# tests/unit/test_available_at.py; only assert_causal's generic contract
# (independent of how the available_at-ish column was produced) is tested
# here.


def test_assert_causal_raises_on_leakage():
    df = pd.DataFrame({
        "feature_ts": [pd.Timestamp("2024-01-01 00:00", tz="UTC")],
        "research_available_at": [pd.Timestamp("2024-01-02 00:00", tz="UTC")],  # after feature ts: leakage
    })
    with pytest.raises(ValueError):
        assert_causal(df, as_of_col="feature_ts")


def test_assert_causal_passes_when_available_before_as_of():
    df = pd.DataFrame({
        "feature_ts": [pd.Timestamp("2024-01-02 00:00", tz="UTC")],
        "research_available_at": [pd.Timestamp("2024-01-01 00:00", tz="UTC")],
    })
    assert_causal(df, as_of_col="feature_ts")  # must not raise
