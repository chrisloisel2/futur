"""
tests/unit/test_build_spot_5m.py
─────────────────────────────────────────────────────────────────────────────
data_v2/normalized/spot_ohlcv/build_spot_5m.py::build_symbol, offline
(fetch_month_1m monkeypatched -- no network).

Same regression as tests/unit/test_build_perp_5m.py (2026-08-11): merging a
newly-fetched month into a YEAR THAT ALREADY HAS an on-disk spot_5m.parquet
crashed with "TypeError: '<' not supported between instances of 'Timestamp'
and 'int'" -- `pd.read_parquet(out_path)` was missing `.set_index("timestamp")`,
so the reloaded frame (RangeIndex) mixed with the freshly-fetched frame
(DatetimeIndex) inside `pd.concat(...).sort_index()`. build_spot_5m.py had
the identical copy-pasted pattern as build_perp_5m.py.

Gate:
    python3 -m pytest tests/unit/test_build_spot_5m.py -q
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.normalized.spot_ohlcv import build_spot_5m


def _fake_month(base_url: str, symbol: str, year: int, month: int, day_start: int = 1, n_days: int = 5) -> pd.DataFrame:
    idx = pd.date_range(f"{year}-{month:02d}-{day_start:02d}", periods=n_days * 24 * 60, freq="1min", tz="UTC")
    return pd.DataFrame({
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
        "quote_asset_volume": 1.0, "number_of_trades": 1, "taker_buy_base_asset_volume": 1.0,
        "taker_buy_quote_asset_volume": 1.0,
    }, index=idx.rename("timestamp"))


@pytest.fixture()
def fake_out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(build_spot_5m, "OUT_DIR", tmp_path)
    return tmp_path


def test_build_symbol_fresh_symbol_no_crash(fake_out_dir, monkeypatch):
    monkeypatch.setattr(build_spot_5m, "fetch_month_1m", _fake_month)
    r = build_spot_5m.build_symbol("FOOUSDT", date(2022, 2, 1), date(2022, 2, 28))
    assert r["new_months"] == 1
    df = pd.read_parquet(fake_out_dir / "symbol=FOOUSDT" / "year=2022" / "spot_5m.parquet")
    assert len(df) > 0
    assert "spot_close" in df.columns and "spot_vwap" in df.columns


def test_build_symbol_merges_new_month_into_existing_year_file(fake_out_dir, monkeypatch):
    """The exact regression: fetch month 2 first (creates year=2022 file),
    then fetch month 1 (an EARLIER month, same year -- out_path already
    exists) -- must not crash, and both months' data must end up present."""
    monkeypatch.setattr(build_spot_5m, "fetch_month_1m", _fake_month)

    r1 = build_spot_5m.build_symbol("FOOUSDT", date(2022, 2, 1), date(2022, 2, 28))
    assert r1["new_months"] == 1

    r2 = build_spot_5m.build_symbol("FOOUSDT", date(2022, 1, 1), date(2022, 1, 31))  # earlier month, same year
    assert r2["new_months"] == 1

    df = pd.read_parquet(fake_out_dir / "symbol=FOOUSDT" / "year=2022" / "spot_5m.parquet")
    assert pd.Timestamp(df["timestamp"].min()).month == 1  # January data actually made it in
    assert pd.Timestamp(df["timestamp"].max()).month == 2  # February data preserved, not overwritten
    assert df["timestamp"].is_monotonic_increasing
    assert not df["timestamp"].duplicated().any()


def test_build_symbol_skips_months_already_in_manifest(fake_out_dir, monkeypatch):
    calls = []
    def fake_fetch(base_url, symbol, y, m):
        calls.append((y, m))
        return _fake_month(base_url, symbol, y, m)
    monkeypatch.setattr(build_spot_5m, "fetch_month_1m", fake_fetch)

    build_spot_5m.build_symbol("FOOUSDT", date(2022, 1, 1), date(2022, 2, 28))
    assert calls == [(2022, 1), (2022, 2)]

    calls.clear()
    r = build_spot_5m.build_symbol("FOOUSDT", date(2022, 1, 1), date(2022, 2, 28))  # same range again
    assert calls == []  # nothing re-fetched -- already in done_months
    assert r["new_months"] == 0


# ── resolve_spot_fetch_window: bound to first_perp_kline_ts, never the ────
# ── composite listing_ts (2026-08-11 fix) ──────────────────────────────────


def test_window_bounds_to_first_perp_kline_ts_when_spot_predates_perp():
    """Spot existed before perp (common: spot markets often predate the
    perp version of the same coin) -- must NOT download the pre-perp
    portion, it's useless for basis."""
    spot_would_have_started = pd.Timestamp("2019-01-01", tz="UTC")  # not even passed in -- proves it's ignored
    first_perp_kline_ts = pd.Timestamp("2021-06-15", tz="UTC")
    window = build_spot_5m.resolve_spot_fetch_window(first_perp_kline_ts, pd.NaT, date(2026, 1, 1))
    assert window is not None
    start, end = window
    assert start == date(2021, 6, 15)
    assert start > spot_would_have_started.date()


def test_window_not_pulled_earlier_by_funding_or_oi_predating_perp():
    """funding_ts/oi_ts observing the symbol before its first perp kline
    must NOT pull spot's start backward -- the function only ever looks at
    first_perp_kline_ts, so an earlier composite listing_ts elsewhere in
    instrument_master can never leak in here."""
    first_perp_kline_ts = pd.Timestamp("2023-02-16", tz="UTC")  # from the real AGIXUSDT-style case
    earlier_funding_ts = pd.Timestamp("2023-02-15 16:00:00.005", tz="UTC")  # composite listing_ts would use this
    assert earlier_funding_ts < first_perp_kline_ts  # sanity: this really is the earlier one

    window = build_spot_5m.resolve_spot_fetch_window(first_perp_kline_ts, pd.NaT, date(2026, 1, 1))
    start, _ = window
    assert start == date(2023, 2, 16)  # first_perp_kline_ts's own date, not the earlier funding date


def test_window_when_spot_starts_after_perp_attempts_from_perp_start(fake_out_dir, monkeypatch):
    """Spot genuinely lists AFTER perp: the window must still start at
    first_perp_kline_ts (so the gap gets attempted and recorded as
    confirmed-404 in the manifest, not silently skipped) -- build_symbol
    then correctly records the pre-spot-listing months as missing_months,
    and later months (once spot genuinely has data) as done_months, with
    no infinite retry of the confirmed-missing prefix on a second run."""
    first_perp_kline_ts = pd.Timestamp("2022-01-01", tz="UTC")
    window = build_spot_5m.resolve_spot_fetch_window(first_perp_kline_ts, pd.NaT, date(2022, 3, 1))
    assert window == (date(2022, 1, 1), date(2022, 3, 1))

    def fake_fetch_spot_lists_march(base_url, symbol, y, m):
        if (y, m) < (2022, 3):
            return None  # confirmed 404: spot didn't exist yet
        return _fake_month(base_url, symbol, y, m)
    monkeypatch.setattr(build_spot_5m, "fetch_month_1m", fake_fetch_spot_lists_march)

    r1 = build_spot_5m.build_symbol("FOOUSDT", *window)
    assert r1["missing_months"] == 2  # Jan, Feb confirmed 404
    assert r1["new_months"] == 1      # March has real data

    manifest = build_spot_5m.load_manifest(fake_out_dir / "symbol=FOOUSDT")
    assert manifest["missing_months"] == ["2022-01", "2022-02"]
    assert manifest["done_months"] == ["2022-03"]

    # re-running the same window must not re-fetch the confirmed-404 months
    calls = []
    monkeypatch.setattr(build_spot_5m, "fetch_month_1m", lambda *a, **k: (calls.append(a) or None))
    r2 = build_spot_5m.build_symbol("FOOUSDT", *window)
    assert calls == []  # Jan/Feb/March all already resolved (missing or done) -- no retry loop
    assert r2["new_months"] == 0


def test_window_fails_closed_when_no_first_perp_kline_ts_proof():
    """No proof perp ever existed for this symbol -- must return None
    (fail closed), never invent a fallback date like the old 2019-09-01
    default."""
    window = build_spot_5m.resolve_spot_fetch_window(pd.NaT, pd.NaT, date(2026, 1, 1))
    assert window is None


def test_window_caps_end_at_delisting_ts():
    first_perp_kline_ts = pd.Timestamp("2021-01-01", tz="UTC")
    delisting_ts = pd.Timestamp("2022-06-15", tz="UTC")
    window = build_spot_5m.resolve_spot_fetch_window(first_perp_kline_ts, delisting_ts, date(2026, 1, 1))
    assert window == (date(2021, 1, 1), date(2022, 6, 15))
