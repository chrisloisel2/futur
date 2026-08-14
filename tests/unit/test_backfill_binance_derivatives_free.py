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

import json
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

    forward_calls = []
    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is not None:
            return pd.DataFrame()  # backward gap-fill: nothing earlier in this test
        forward_calls.append(start_ms)
        return _rows(["2024-01-02 00:00"])
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    start_ms_floor = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    result = bf.top_up_funding("FOOUSDT", start_ms_floor)

    last_existing_ms = int(existing["timestamp"].max().value // 1_000_000)
    assert forward_calls == [last_existing_ms + 1]  # fetched from the last on-disk row forward, NOT from 2021
    assert len(result) == 4  # 3 existing + 1 new
    assert result["timestamp"].is_monotonic_increasing


def test_top_up_first_time_symbol_uses_start_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    (tmp_path / "funding").mkdir(parents=True)

    calls = []
    def fake_backfill_funding(sym, start_ms, end_ms=None):
        calls.append(start_ms)
        return _rows(["2021-01-01 00:00"])
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    start_ms_floor = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    result = bf.top_up_funding("NEWUSDT", start_ms_floor)
    # no existing file at all -- backward gap-fill has nothing to anchor
    # against and is skipped entirely; only the forward fetch runs.
    assert calls == [start_ms_floor]
    assert len(result) == 1


def test_top_up_zero_new_rows_still_returns_full_existing_history(tmp_path, monkeypatch):
    """A top-up run right after the last real settlement (nothing new yet)
    must not truncate the store to empty."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00"])
    existing.to_parquet(symbol_dir / "FOOUSDT.parquet", index=False)
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms, end_ms=None: pd.DataFrame())

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

    forward_calls = []
    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is not None:
            return pd.DataFrame()  # backward gap-fill: nothing earlier in this test
        forward_calls.append(start_ms)
        return _rows(["2024-06-01 00:00"])  # would-be phantom post-delisting feed
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    delisting_ts = pd.Timestamp("2024-01-01 08:00", tz="UTC")
    result = bf.top_up_funding(
        "DEADUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000),
        delisting_ts=delisting_ts,
    )
    assert forward_calls == []  # no forward fetch issued -- nothing legitimate left past delisting_ts
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

    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is not None:
            return pd.DataFrame()  # backward gap-fill: nothing earlier in this test
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
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms, end_ms=None: pd.DataFrame())

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
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms, end_ms=None: _rows(["2026-08-11 00:00"]))

    result = bf.top_up_funding(
        "LIVEUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000),
        delisting_ts=None,
    )
    assert len(result) == 2
    assert result["timestamp"].max() == pd.Timestamp("2026-08-11", tz="UTC")


# ── top_up_funding: genuinely bidirectional -- a too-late start_ms from an
# earlier run must self-heal on a later run with a corrected start_ms
# (found 2026-08-14: real Binance funding data exists well before the
# single global --start default of 2021-01-01 for any symbol listed
# earlier; AAVEUSDT specifically has real settlements from 2020-10-16) ──


def test_top_up_backfills_a_gap_before_the_existing_files_own_first_row(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2021-01-01 00:00", "2021-01-01 08:00"])
    existing.to_parquet(symbol_dir / "AAVEUSDT.parquet", index=False)

    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is not None:
            # backward call: real early history the earlier run never fetched
            return _rows(["2020-10-16 00:00", "2020-10-16 08:00"])
        return pd.DataFrame()  # forward: nothing new yet
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    earlier_start_ms = int(pd.Timestamp("2020-10-16", tz="UTC").timestamp() * 1000)
    result = bf.top_up_funding("AAVEUSDT", earlier_start_ms)
    assert len(result) == 4  # 2 existing + 2 recovered early rows
    assert result["timestamp"].min() == pd.Timestamp("2020-10-16 00:00", tz="UTC")
    assert result["timestamp"].is_monotonic_increasing


def test_top_up_backward_fill_is_bounded_by_the_existing_files_own_first_row(tmp_path, monkeypatch):
    """The backward gap-fill must never keep or duplicate a row at/after
    the existing file's own first timestamp -- only the genuine gap
    before it."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2021-01-01 00:00"])
    existing.to_parquet(symbol_dir / "AAVEUSDT.parquet", index=False)

    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is not None:
            # includes a row AT the existing boundary -- must be dropped, not duplicated
            return _rows(["2020-10-16 00:00", "2021-01-01 00:00"])
        return pd.DataFrame()
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    earlier_start_ms = int(pd.Timestamp("2020-10-16", tz="UTC").timestamp() * 1000)
    result = bf.top_up_funding("AAVEUSDT", earlier_start_ms)
    assert len(result) == 2  # the one genuinely new early row + the one original row, no duplicate
    assert (result["timestamp"] == pd.Timestamp("2021-01-01 00:00", tz="UTC")).sum() == 1


def test_top_up_no_backward_fill_when_start_ms_not_earlier_than_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2021-01-01 00:00"])
    existing.to_parquet(symbol_dir / "AAVEUSDT.parquet", index=False)

    backward_calls = []
    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is not None:
            backward_calls.append((start_ms, end_ms))
        return pd.DataFrame()
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)

    later_start_ms = int(pd.Timestamp("2022-01-01", tz="UTC").timestamp() * 1000)  # later than existing's own start
    bf.top_up_funding("AAVEUSDT", later_start_ms)
    assert backward_calls == []  # nothing earlier is being requested -- no backward call at all


# ── symbol_start_ms: each symbol's own real listing bound, not a single
# global --start applied to every symbol regardless of when it listed ───


def test_symbol_start_ms_uses_first_perp_kline_ts_when_earlier_than_fallback():
    im = pd.DataFrame([{"symbol": "AAVEUSDT", "first_perp_kline_ts": pd.Timestamp("2020-10-16", tz="UTC")}])
    fallback_ms = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    result = bf.symbol_start_ms("AAVEUSDT", im, fallback_ms)
    assert result == int(pd.Timestamp("2020-10-16", tz="UTC").timestamp() * 1000)


def test_symbol_start_ms_never_later_than_fallback():
    im = pd.DataFrame([{"symbol": "NEWUSDT", "first_perp_kline_ts": pd.Timestamp("2024-01-01", tz="UTC")}])
    fallback_ms = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    result = bf.symbol_start_ms("NEWUSDT", im, fallback_ms)
    assert result == fallback_ms  # symbol's own bound is LATER -- never regress past the CLI floor


def test_symbol_start_ms_falls_back_when_field_missing():
    im = pd.DataFrame([{"symbol": "FOOUSDT", "first_perp_kline_ts": pd.NaT}])
    fallback_ms = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    assert bf.symbol_start_ms("FOOUSDT", im, fallback_ms) == fallback_ms


def test_symbol_start_ms_falls_back_when_im_is_none():
    fallback_ms = int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000)
    assert bf.symbol_start_ms("FOOUSDT", None, fallback_ms) == fallback_ms


# ── top_up_funding: confirmed-unavailable manifest tracking (2026-08-14,
# user-authorized) -- funding never had a manifest before, unlike OI/perp/
# spot/aggTrades, so a genuinely-stopped symbol (AGIXUSDT: real empty API
# response from its stored cursor forward, verified against live Binance,
# not the 2026-08-11 phantom-feed bug) could never be excluded from
# staleness/coverage ──────────────────────────────────────────────────


def test_top_up_writes_confirmed_empty_manifest_on_genuine_empty_forward_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2025-06-19 08:00"])
    existing.to_parquet(symbol_dir / "AGIXUSDT.parquet", index=False)
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms, end_ms=None: pd.DataFrame())
    fixed_now = pd.Timestamp("2026-08-14", tz="UTC")
    monkeypatch.setattr(bf.time, "time", lambda: fixed_now.timestamp())

    bf.top_up_funding("AGIXUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000), delisting_ts=None)

    manifest_path = symbol_dir / "AGIXUSDT_manifest.json"
    assert manifest_path.exists()
    m = json.loads(manifest_path.read_text())
    assert pd.Timestamp(m["confirmed_empty_from"]) == pd.Timestamp("2025-06-19 08:00", tz="UTC") + pd.Timedelta(milliseconds=1)
    assert pd.Timestamp(m["confirmed_as_of"]) == fixed_now


def test_top_up_clears_confirmed_empty_manifest_when_new_data_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2025-06-19 08:00"])
    existing.to_parquet(symbol_dir / "AGIXUSDT.parquet", index=False)
    manifest_path = symbol_dir / "AGIXUSDT_manifest.json"
    manifest_path.write_text('{"confirmed_empty_from": "2025-06-19T08:00:00", "confirmed_as_of": "2025-07-01T00:00:00"}')
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms, end_ms=None: _rows(["2025-08-01 00:00"]))
    monkeypatch.setattr(bf.time, "time", lambda: pd.Timestamp("2026-08-14", tz="UTC").timestamp())

    bf.top_up_funding("AGIXUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000), delisting_ts=None)

    assert not manifest_path.exists()  # stale claim invalidated -- real new data proves it wrong


def test_top_up_does_not_write_manifest_for_a_delisted_symbol(tmp_path, monkeypatch):
    """A delisted symbol's trailing bound is already handled by
    delisting_ts itself -- no separate manifest claim needed or written."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    existing = _rows(["2024-01-01 00:00", "2024-01-01 08:00"])
    existing.to_parquet(symbol_dir / "DEADUSDT.parquet", index=False)
    monkeypatch.setattr(bf, "backfill_funding", lambda sym, start_ms, end_ms=None: pd.DataFrame())
    monkeypatch.setattr(bf.time, "time", lambda: pd.Timestamp("2026-08-14", tz="UTC").timestamp())

    delisting_ts = pd.Timestamp("2024-01-01 08:00", tz="UTC")
    bf.top_up_funding(
        "DEADUSDT", int(pd.Timestamp("2021-01-01", tz="UTC").timestamp() * 1000), delisting_ts=delisting_ts,
    )

    assert not (symbol_dir / "DEADUSDT_manifest.json").exists()


def test_top_up_does_not_write_manifest_when_nothing_to_check_yet(tmp_path, monkeypatch):
    """fetch_from_ms already at/past "now" -- no real forward fetch
    attempt was made, so there is nothing to confirm either way."""
    monkeypatch.setattr(bf, "OUT", tmp_path)
    symbol_dir = tmp_path / "funding"
    symbol_dir.mkdir(parents=True)
    now = pd.Timestamp("2026-08-14", tz="UTC")
    existing = _rows([now.isoformat()])
    existing.to_parquet(symbol_dir / "FRESHUSDT.parquet", index=False)
    forward_calls = []
    def fake_backfill_funding(sym, start_ms, end_ms=None):
        if end_ms is None:  # forward call only -- backward gap-fill is not this test's concern
            forward_calls.append(start_ms)
        return pd.DataFrame()
    monkeypatch.setattr(bf, "backfill_funding", fake_backfill_funding)
    monkeypatch.setattr(bf.time, "time", lambda: now.timestamp())

    # start_ms == existing's own first row -- no backward gap to fill either
    bf.top_up_funding("FRESHUSDT", int(now.timestamp() * 1000), delisting_ts=None)

    assert forward_calls == []  # fetch_from_ms (now+1ms) already >= now_ms -- no attempt made
    assert not (symbol_dir / "FRESHUSDT_manifest.json").exists()
