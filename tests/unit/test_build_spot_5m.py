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
