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


def test_load_delisting_map_only_includes_confirmed_delisted_symbols(tmp_path, monkeypatch):
    im_path = tmp_path / "instrument_master.parquet"
    pd.DataFrame({
        "symbol": ["DEADUSDT", "LIVEUSDT"],
        "delisting_ts": [pd.Timestamp("2024-01-01", tz="UTC"), pd.NaT],
    }).to_parquet(im_path, index=False)
    monkeypatch.setattr(bf, "INSTRUMENT_MASTER", im_path)
    out = bf.load_delisting_map()
    assert list(out.keys()) == ["DEADUSDT"]
    assert out["DEADUSDT"] == pd.Timestamp("2024-01-01", tz="UTC")


def test_load_delisting_map_empty_without_instrument_master(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "INSTRUMENT_MASTER", tmp_path / "does_not_exist.parquet")
    assert bf.load_delisting_map() == {}


# ── top_up_funding: delisted symbols must never gain rows past the proven
# delisting_ts -- Binance's fundingRate endpoint keeps emitting a frozen
# placeholder feed forever instead of 404ing after delisting (found
# 2026-08-11 on EOSUSDT/MATICUSDT/SXPUSDT: 61 fake constant-rate rows each,
# fresh timestamps right up to "now") ───────────────────────────────────


def test_top_up_does_not_fetch_past_a_symbol_already_fully_covered_to_delisting(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00"])  # already reaches delisting_ts
    existing.to_parquet(symbol_dir / "DEADUSDT.parquet", index=False)

    calls = []
    def fake_backfill_funding(sym, start_ms):
        calls.append(start_ms)
        return _rows(["2024-06-01 00:00"])  # would-be phantom post-delisting feed
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    delisting_ts = pd.Timestamp("2024-01-01 08:00", tz="UTC")
    result = bf.top_up_funding(
        "DEADUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000),
        delisting_ts=delisting_ts,
    )
    assert calls == []  # no fetch issued at all -- nothing legitimate left past delisting_ts
    assert len(result) == 2
    assert result["timestamp"].max() == delisting_ts


def test_top_up_strips_a_freshly_fetched_row_past_delisting_ts(tmp_path, monkeypatch):
    """Even if a fetch IS issued (existing data stops short of delisting_ts),
    any row the API returns past delisting_ts must be discarded -- this is
    the exact fake-feed pattern: the API doesn't stop at the real boundary."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00"])
    existing.to_parquet(symbol_dir / "DEADUSDT.parquet", index=False)

    def fake_backfill_funding(sym, start_ms):
        # real row up to delisting, then phantom rows the exchange keeps emitting
        return _rows(["2024-01-01 08:00", "2024-06-01 00:00", "2024-12-01 00:00"])
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    delisting_ts = pd.Timestamp("2024-01-01 08:00", tz="UTC")
    result = bf.top_up_funding(
        "DEADUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000),
        delisting_ts=delisting_ts,
    )
    assert len(result) == 2  # the 2024-01-01 00:00 + 08:00 rows only
    assert result["timestamp"].max() == delisting_ts


def test_top_up_self_heals_existing_contamination_past_delisting_ts(tmp_path, monkeypatch):
    """Reproduces the exact EOSUSDT/MATICUSDT/SXPUSDT bug: on-disk data was
    already contaminated with fake rows past delisting_ts by a pre-fix run.
    The very next run (no new fetch needed) must strip them -- self-healing,
    no separate cleanup script or hardcoded symbol required."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    contaminated = _rows([
        "2024-01-01 00:00", "2024-01-01 08:00",  # real
        "2024-01-01 16:00", "2024-01-02 00:00",  # phantom post-delisting feed
    ])
    contaminated.to_parquet(symbol_dir / "DEADUSDT.parquet", index=False)
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms: pd.DataFrame())

    delisting_ts = pd.Timestamp("2024-01-01 08:00", tz="UTC")
    result = bf.top_up_funding(
        "DEADUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000),
        delisting_ts=delisting_ts,
    )
    assert len(result) == 2  # the 2 phantom rows are gone
    assert result["timestamp"].max() == delisting_ts


def test_top_up_delisting_ts_none_behaves_exactly_as_before(tmp_path, monkeypatch):
    """A symbol with no confirmed delisting_ts (the common case, still
    trading) must be completely unaffected by this fix."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00"])
    existing.to_parquet(symbol_dir / "LIVEUSDT.parquet", index=False)
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms: _rows(["2026-08-11 00:00"]))

    result = bf.top_up_funding(
        "LIVEUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000),
        delisting_ts=None,
    )
    assert len(result) == 2
    assert result["timestamp"].max() == pd.Timestamp("2026-08-11", tz="UTC")
