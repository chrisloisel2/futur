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


def _baseline(listing_ts):
    """What _expected_start_baseline would compute for a generic (non-spot)
    dataset -- these tests exercise _confirmed_unavailable_expected_start
    directly, which now takes the baseline as an explicit argument (the
    caller, evaluate_dataset_symbol, computes it via _expected_start_baseline
    using each dataset's own listing_ts_field -- see test_data_v2_readiness_
    spot_bound.py for the spot-specific first_perp_kline_ts behavior)."""
    return None if pd.isna(listing_ts) else pd.Timestamp(listing_ts)


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

    result = readiness_mod._confirmed_unavailable_expected_start(
        "oi_vision_5m", "ADAUSDT", im, df, "create_time", _baseline(TS("2020-01-31")),
    )
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

    result = readiness_mod._confirmed_unavailable_expected_start(
        "oi_vision_5m", "FOOUSDT", im, df, "create_time", _baseline(TS("2020-01-31")),
    )
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

    result = readiness_mod._confirmed_unavailable_expected_start(
        "oi_vision_5m", "FOOUSDT", im, df, "create_time", _baseline(TS("2021-12-01")),
    )
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

    result = readiness_mod._confirmed_unavailable_expected_start(
        "agg_trades_flow_1m", "FOOUSDT", im, df, "timestamp", _baseline(TS("2020-01-01")),
    )
    assert result == df["timestamp"].min()


def test_funding_has_no_manifest_spec_so_never_adjusted(monkeypatch):
    im = _im_row("FOOUSDT", TS("2020-01-01"))
    df = pd.DataFrame({"timestamp": pd.date_range("2021-01-01", periods=5, freq="8h", tz="UTC")})
    monkeypatch.setattr(readiness_mod, "DATASET_MANIFEST_SPECS", {})  # funding not in DATASET_MANIFEST_SPECS

    result = readiness_mod._confirmed_unavailable_expected_start(
        "funding", "FOOUSDT", im, df, "timestamp", _baseline(TS("2020-01-01")),
    )
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

    result = readiness_mod._confirmed_unavailable_expected_start(
        "oi_vision_5m", "FOOUSDT", im, df, "create_time", _baseline(pd.NaT),
    )
    assert result is None


# ── _expected_start_baseline: spot_5m uses first_perp_kline_ts, not the ───
# ── composite listing_ts (2026-08-11 fix) ──────────────────────────────────


def _im_row_v2(symbol: str, *, listing_ts, first_perp_kline_ts) -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": symbol, "listing_ts": listing_ts, "first_perp_kline_ts": first_perp_kline_ts,
        "delisting_ts": pd.NaT,
    }])


def test_expected_start_baseline_spot_uses_first_perp_kline_ts_not_listing_ts():
    # composite listing_ts is EARLIER (e.g. funding observed the symbol
    # first) -- spot's baseline must ignore that and use first_perp_kline_ts.
    im = _im_row_v2("AGIXUSDT", listing_ts=TS("2023-02-15 07:00:00"), first_perp_kline_ts=TS("2023-02-16"))
    result = readiness_mod._expected_start_baseline("spot_5m", "AGIXUSDT", im)
    assert result == TS("2023-02-16")


def test_expected_start_baseline_non_spot_dataset_uses_generic_listing_ts():
    im = _im_row_v2("AGIXUSDT", listing_ts=TS("2023-02-15 07:00:00"), first_perp_kline_ts=TS("2023-02-16"))
    result = readiness_mod._expected_start_baseline("perp_5m", "AGIXUSDT", im)
    assert result == TS("2023-02-15 07:00:00")


def test_expected_start_baseline_spot_fails_closed_when_first_perp_kline_ts_missing():
    im = _im_row_v2("FOOUSDT", listing_ts=TS("2023-02-15"), first_perp_kline_ts=pd.NaT)
    result = readiness_mod._expected_start_baseline("spot_5m", "FOOUSDT", im)
    assert result is None  # never falls back to the generic listing_ts


# ── _monthly_publication_watermark / _expected_end_baseline: perp/spot's
# coverage denominator must not demand data through "now" for a source
# that structurally cannot have the current, still-open month yet ────────


def test_monthly_watermark_returns_last_month_end_once_past_its_publication_lag():
    # now is well past July's close (07-31) + 5d lag (08-05) -- July is published
    now = TS("2026-08-14")
    result = readiness_mod._monthly_publication_watermark(now)
    assert result == TS("2026-07-31 23:55:00")


def test_monthly_watermark_falls_back_a_month_when_still_inside_the_lag_window():
    # now is 08-03: July closed 07-31 but its 5d lag (until 08-05) hasn't
    # cleared yet -- July's archive isn't genuinely published, fall back to June.
    now = TS("2026-08-03")
    result = readiness_mod._monthly_publication_watermark(now)
    assert result == TS("2026-06-30 23:55:00")


def test_monthly_watermark_never_returns_the_still_open_current_month():
    now = TS("2026-08-14")
    result = readiness_mod._monthly_publication_watermark(now)
    assert result.month != 8 or result.year != 2026


def test_expected_end_baseline_uses_watermark_for_monthly_datasets():
    im = _im_row_v2("AGIXUSDT", listing_ts=TS("2023-02-15"), first_perp_kline_ts=TS("2023-02-16"))
    now = TS("2026-08-14")
    result = readiness_mod._expected_end_baseline("perp_5m", "AGIXUSDT", im, now)
    assert result == TS("2026-07-31 23:55:00")


def test_expected_end_baseline_none_for_non_monthly_dataset_unchanged_behavior():
    im = _im_row_v2("AGIXUSDT", listing_ts=TS("2023-02-15"), first_perp_kline_ts=TS("2023-02-16"))
    now = TS("2026-08-14")
    result = readiness_mod._expected_end_baseline("funding", "AGIXUSDT", im, now)
    assert result is None  # validate_series' own now-fallback still applies, untouched


def test_expected_end_baseline_none_when_symbol_confirmed_delisted():
    # delisting_ts is a real, tighter bound than the publication watermark
    # and must take priority -- validate_series' own internal fallback
    # (delisting_ts if known, else now) already handles this correctly,
    # so the override here must step aside (return None) rather than
    # clobber it with the watermark.
    im = pd.DataFrame([{
        "symbol": "DEADUSDT", "listing_ts": TS("2023-02-15"), "first_perp_kline_ts": TS("2023-02-16"),
        "delisting_ts": TS("2024-01-01"),
    }])
    now = TS("2026-08-14")
    result = readiness_mod._expected_end_baseline("perp_5m", "DEADUSDT", im, now)
    assert result is None
