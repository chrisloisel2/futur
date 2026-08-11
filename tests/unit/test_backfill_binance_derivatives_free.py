"""
tests/unit/test_backfill_binance_derivatives_free.py
─────────────────────────────────────────────────────────────────────────────
scripts/backfill_binance_derivatives_free.py: the canonical funding top-up
(writes to data/derivatives_backfill/binance/funding/{symbol}.parquet, the
exact path reports/DATA_V2_READINESS.json reads -- verified by repo
inspection before use, since scripts/collect_funding_rate_binance.py looks
similar but writes elsewhere).

Fix (2026-08-11): an earlier version always refetched --start..now and
overwrote the file outright -- safe only as long as Binance's API keeps
serving full multi-year history. Now genuinely incremental (top_up_funding
fetches only from the existing file's last timestamp forward) and never
loses on-disk history even if a run fetches zero new rows.

Gate:
    python3 -m pytest tests/unit/test_backfill_binance_derivatives_free.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import backfill_binance_derivatives_free as bf


def _rows(timestamps, rates=None) -> pd.DataFrame:
    ts = pd.to_datetime(timestamps, utc=True)
    return pd.DataFrame({
        "timestamp": ts,
        "funding_rate": rates or [0.0001] * len(ts),
        "mark_price": [100.0] * len(ts),
    })


# ── merge_funding ───────────────────────────────────────────────────────


def test_merge_funding_no_overlap_unions_and_sorts():
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00"])
    new = _rows(["2024-01-01 16:00", "2024-01-02 00:00"])
    out = bf.merge_funding(existing, new)
    assert len(out) == 4
    assert out["timestamp"].is_monotonic_increasing


def test_merge_funding_overlap_deduplicates_keeping_new_value():
    existing = _rows(["2024-01-01 00:00"], rates=[0.0001])
    new = _rows(["2024-01-01 00:00"], rates=[0.9999])  # same timestamp, different value
    out = bf.merge_funding(existing, new)
    assert len(out) == 1
    assert out["funding_rate"].iloc[0] == pytest.approx(0.9999)  # new fetch wins on exact clash


def test_merge_funding_new_empty_never_loses_existing_history():
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00", "2024-01-01 16:00"])
    out = bf.merge_funding(existing, pd.DataFrame())
    assert len(out) == 3  # a zero-new-rows run must not shrink the on-disk store


def test_merge_funding_existing_none_returns_just_new():
    new = _rows(["2024-01-01 00:00"])
    out = bf.merge_funding(None, new)
    assert len(out) == 1


def test_merge_funding_both_empty_returns_empty():
    out = bf.merge_funding(None, pd.DataFrame())
    assert out.empty


# ── top_up_funding: genuinely incremental, not a full re-fetch ───────────


def test_top_up_fetches_only_from_last_existing_timestamp_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00", "2024-01-01 16:00"])
    existing.to_parquet(symbol_dir / "FOOUSDT.parquet", index=False)

    calls = []
    def fake_backfill_funding(sym, start_ms):
        calls.append(start_ms)
        return _rows(["2024-01-02 00:00"])
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    start_ms_floor = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    result = bf.top_up_funding("FOOUSDT", start_ms_floor)

    last_existing_ms = int(existing["timestamp"].max().value // 1_000_000)
    assert calls == [last_existing_ms + 1]  # fetched from the last on-disk row forward, NOT from 2021
    assert len(result) == 4  # 3 existing + 1 new
    assert result["timestamp"].is_monotonic_increasing


def test_top_up_first_time_symbol_uses_start_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    (tmp_path / "funding").mkdir(parents=True)

    calls = []
    def fake_backfill_funding(sym, start_ms):
        calls.append(start_ms)
        return _rows(["2021-01-01 00:00"])
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    start_ms_floor = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    result = bf.top_up_funding("NEWUSDT", start_ms_floor)
    assert calls == [start_ms_floor]  # no existing file -- fetch from the requested floor
    assert len(result) == 1


def test_top_up_zero_new_rows_still_returns_full_existing_history(tmp_path, monkeypatch):
    """A top-up run right after the last real settlement (nothing new yet)
    must not truncate the store to empty."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00"])
    existing.to_parquet(symbol_dir / "FOOUSDT.parquet", index=False)
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms: pd.DataFrame())

    result = bf.top_up_funding("FOOUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000))
    assert len(result) == 2


# ── default_symbols ────────────────────────────────────────────────────


def test_default_symbols_reads_full_pit_universe(tmp_path, monkeypatch):
    im_path = tmp_path / "instrument_master.parquet"
    pd.DataFrame({"symbol": ["ZZZUSDT", "AAAUSDT", "AAAUSDT"]}).to_parquet(im_path, index=False)
    monkeypatch.setattr(bf, "INSTRUMENT_MASTER", im_path)
    assert bf.default_symbols() == ["AAAUSDT", "ZZZUSDT"]  # deduped and sorted, not the 9-symbol core list


def test_default_symbols_falls_back_to_core_list_without_instrument_master(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "INSTRUMENT_MASTER", tmp_path / "does_not_exist.parquet")
    assert bf.default_symbols() == bf.CORE_SYMBOLS
