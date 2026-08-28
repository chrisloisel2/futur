"""
tests/unit/test_instrument_master.py
─────────────────────────────────────────────────────────────────────────────
InstrumentMaster V2 reconciliation (data_v2/instruments/build_instrument_
master.py): four independent proofs of existence (exchangeInfo onboardDate,
perp klines, funding, OI) reconciled into one canonical listing_ts/
delisting_ts, without ever trusting onboardDate alone (the AIAUSDT case:
real funding history starts ~4 months before its exchangeInfo onboardDate)
and without ever inventing a delisting_ts that isn't demonstrated.

Gate:
    python3 -m pytest tests/unit/test_instrument_master.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from data_v2.instruments import build_instrument_master as im_mod

TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


# ── reconcile_start ─────────────────────────────────────────────────────


def test_reconcile_start_picks_the_earliest_available_source():
    ts, source, conflict = im_mod.reconcile_start(
        exchangeinfo_onboard_ts=TS("2026-01-20"),
        first_perp_kline_ts=pd.NaT,
        first_funding_ts=TS("2025-09-18"),  # the real AIAUSDT case
        first_oi_ts=TS("2026-01-20 02:25"),
    )
    assert ts == TS("2025-09-18")
    assert source == "first_funding_ts"
    assert conflict is True  # ~4 months apart, way over the 24h grace


def test_reconcile_start_no_conflict_within_grace_period():
    # OI genuinely starting ~9h before onboardDate is normal PIT-boundary
    # slop, not a conflict -- must not be flagged.
    ts, source, conflict = im_mod.reconcile_start(
        exchangeinfo_onboard_ts=TS("2026-01-20 11:15"),
        first_perp_kline_ts=pd.NaT,
        first_funding_ts=pd.NaT,
        first_oi_ts=TS("2026-01-20 02:25"),
    )
    assert ts == TS("2026-01-20 02:25")
    assert source == "first_oi_ts"
    assert conflict is False


def test_reconcile_start_exact_boundary_not_a_conflict():
    ts, source, conflict = im_mod.reconcile_start(
        exchangeinfo_onboard_ts=TS("2026-01-21"),  # exactly 24h later
        first_perp_kline_ts=pd.NaT,
        first_funding_ts=pd.NaT,
        first_oi_ts=TS("2026-01-20"),
    )
    assert conflict is False  # spread == threshold, not >


def test_reconcile_start_single_source_never_a_conflict():
    ts, source, conflict = im_mod.reconcile_start(
        exchangeinfo_onboard_ts=TS("2026-01-20"),
        first_perp_kline_ts=pd.NaT, first_funding_ts=pd.NaT, first_oi_ts=pd.NaT,
    )
    assert ts == TS("2026-01-20")
    assert source == "exchangeinfo_onboard_ts"
    assert conflict is False


def test_reconcile_start_no_source_at_all_is_unresolved():
    ts, source, conflict = im_mod.reconcile_start(pd.NaT, pd.NaT, pd.NaT, pd.NaT)
    assert pd.isna(ts)
    assert source is None
    assert conflict is False


def test_reconcile_start_tie_breaks_by_priority_order():
    # exchangeInfo and perp klines exactly agree -- exchangeinfo_onboard_ts
    # wins the label deterministically (first in priority order), not
    # whichever happens to be first in a dict.
    ts, source, conflict = im_mod.reconcile_start(
        exchangeinfo_onboard_ts=TS("2026-01-20"),
        first_perp_kline_ts=TS("2026-01-20"),
        first_funding_ts=pd.NaT, first_oi_ts=pd.NaT,
    )
    assert source == "exchangeinfo_onboard_ts"


# ── reconcile_end ───────────────────────────────────────────────────────


def test_reconcile_end_sets_delisting_only_when_absent_from_exchangeinfo():
    last_proven, delisting = im_mod.reconcile_end(
        exchangeinfo_status="ABSENT",
        last_perp_kline_ts=TS("2024-05-01"), last_funding_ts=TS("2024-04-20"), last_oi_ts=pd.NaT,
    )
    assert last_proven == TS("2024-05-01")  # max of the available sources
    assert delisting == TS("2024-05-01")


def test_reconcile_end_never_invents_delisting_for_a_live_status():
    # TRADING, SETTLING, PENDING_TRADING, BREAK -- any status still present
    # in exchangeInfo means "still alive", however stale the last data row.
    for status in ("TRADING", "SETTLING", "PENDING_TRADING", "BREAK"):
        last_proven, delisting = im_mod.reconcile_end(
            exchangeinfo_status=status,
            last_perp_kline_ts=TS("2020-01-01"),  # very stale
            last_funding_ts=pd.NaT, last_oi_ts=pd.NaT,
        )
        assert last_proven == TS("2020-01-01")  # still tracked/reported
        assert pd.isna(delisting)  # but NEVER turned into a delisting_ts


def test_reconcile_end_absent_but_no_data_at_all_stays_unresolved():
    last_proven, delisting = im_mod.reconcile_end(
        exchangeinfo_status="ABSENT",
        last_perp_kline_ts=pd.NaT, last_funding_ts=pd.NaT, last_oi_ts=pd.NaT,
    )
    assert pd.isna(last_proven)
    assert pd.isna(delisting)  # nothing to prove a delisting date from


# ── full build() integration, with a fake on-disk universe ─────────────


@pytest.fixture()
def fake_universe(tmp_path, monkeypatch):
    perp_dir = tmp_path / "perp_5m/venue=binance"
    klines_1d_dir = tmp_path / "um_klines_1d"
    funding_dir = tmp_path / "funding"
    oi_dir = tmp_path / "oi_metrics"
    manifest_path = tmp_path / "PIT_UNIVERSE_MANIFEST.json"
    for d in (perp_dir, klines_1d_dir, funding_dir, oi_dir):
        d.mkdir(parents=True)

    monkeypatch.setattr(im_mod, "PERP_5M_DIR", perp_dir)
    monkeypatch.setattr(im_mod, "KLINES_1D_DIR", klines_1d_dir)
    monkeypatch.setattr(im_mod, "FUNDING_DIR", funding_dir)
    monkeypatch.setattr(im_mod, "OI_METRICS_DIR", oi_dir)
    monkeypatch.setattr(im_mod, "UNIVERSE_MANIFEST", manifest_path)
    return {"perp": perp_dir, "klines_1d": klines_1d_dir, "funding": funding_dir,
            "oi": oi_dir, "manifest": manifest_path}


def _write_manifest(manifest_path: Path, symbols: list[str]) -> None:
    import json
    manifest_path.write_text(json.dumps({"symbols_ever_member": symbols}))


def _write_funding(funding_dir: Path, symbol: str, start: str, end: str) -> None:
    idx = pd.date_range(start, end, freq="8h", tz="UTC")
    pd.DataFrame({"timestamp": idx, "funding_rate": 0.0001, "mark_price": 100.0}).to_parquet(
        funding_dir / f"{symbol}.parquet", index=False
    )


def _write_oi(oi_dir: Path, symbol: str, start: str, end: str) -> None:
    idx = pd.date_range(start, end, freq="5min", tz="UTC")
    pd.DataFrame({"create_time": idx, "symbol": symbol, "sum_open_interest": 1000.0}).to_parquet(
        oi_dir / f"{symbol}_metrics_5m.parquet", index=False
    )


def test_build_pushes_back_listing_ts_for_aiausdt_like_case(fake_universe, monkeypatch):
    _write_manifest(fake_universe["manifest"], ["AIAUSDT"])
    monkeypatch.setattr(im_mod, "fetch_exchange_info", lambda: {
        "AIAUSDT": {
            "base": "AIA", "quote": "USDT", "status": "TRADING", "contract_type": "PERPETUAL",
            "onboard_ts": TS("2026-01-20 11:15"),
            "tick_size": 0.00001, "step_size": 1.0, "min_notional": 5.0,
        }
    })
    _write_funding(fake_universe["funding"], "AIAUSDT", "2025-09-18", "2026-07-22")
    _write_oi(fake_universe["oi"], "AIAUSDT", "2026-01-20 02:25", "2026-08-08")

    out = im_mod.build()
    row = out.loc[out["symbol"] == "AIAUSDT"].iloc[0]
    assert row["listing_ts"] == TS("2025-09-18")
    assert row["listing_ts_source"] == "first_funding_ts"
    assert row["metadata_conflict"] == True  # noqa: E712
    assert row["exchangeinfo_onboard_ts"] == TS("2026-01-20 11:15")
    assert pd.isna(row["delisting_ts"])  # still TRADING -- must stay alive


def test_build_confirms_delisting_only_when_absent(fake_universe, monkeypatch):
    _write_manifest(fake_universe["manifest"], ["DEADUSDT"])
    monkeypatch.setattr(im_mod, "fetch_exchange_info", lambda: {})  # DEADUSDT absent
    _write_funding(fake_universe["funding"], "DEADUSDT", "2021-01-01", "2021-06-01")

    out = im_mod.build()
    row = out.loc[out["symbol"] == "DEADUSDT"].iloc[0]
    assert row["exchangeinfo_status"] == im_mod.ABSENT
    assert row["listing_ts"] == TS("2021-01-01")
    assert row["delisting_ts"] == TS("2021-06-01")  # proven via last_funding_ts, confirmed by ABSENT
    assert row["valid_until"] == row["delisting_ts"]


def test_build_settling_symbol_not_falsely_delisted(fake_universe, monkeypatch):
    """The exact bug this replaces: a SETTLING symbol must stay alive, not
    fall back to a klines-bounds-derived delisting_ts."""
    _write_manifest(fake_universe["manifest"], ["SETLUSDT"])
    monkeypatch.setattr(im_mod, "fetch_exchange_info", lambda: {
        "SETLUSDT": {
            "base": "SETL", "quote": "USDT", "status": "SETTLING", "contract_type": "PERPETUAL",
            "onboard_ts": TS("2023-01-01"),
            "tick_size": 0.01, "step_size": 1.0, "min_notional": 5.0,
        }
    })
    idx = pd.date_range("2023-01-01", periods=5, freq="1D", tz="UTC")
    pd.DataFrame({"open_time": idx, "close": 1.0}).to_parquet(
        fake_universe["klines_1d"] / "SETLUSDT_1d.parquet", index=False
    )

    out = im_mod.build()
    row = out.loc[out["symbol"] == "SETLUSDT"].iloc[0]
    assert row["exchangeinfo_status"] == "SETTLING"
    assert pd.isna(row["delisting_ts"])


def test_build_unresolved_symbol_has_no_listing_ts(fake_universe, monkeypatch):
    _write_manifest(fake_universe["manifest"], ["GHOSTUSDT"])
    monkeypatch.setattr(im_mod, "fetch_exchange_info", lambda: {})

    out = im_mod.build()
    row = out.loc[out["symbol"] == "GHOSTUSDT"].iloc[0]
    assert pd.isna(row["listing_ts"])
    assert row["listing_ts_source"] is None
    assert pd.isna(row["delisting_ts"])


def test_build_excludes_source_wide_floor_from_reconciliation(fake_universe, monkeypatch):
    """Round-1 fix found while first running this against the real corpus:
    first_oi_ts landed on the EXACT same instant for 104/312 real symbols
    (a backfill floor, not per-symbol proof) -- any symbol whose OI data
    happens to start on the detected floor must not have that value treated
    as proof of its own listing, even if the floor happens to be earlier
    than its exchangeInfo onboardDate."""
    symbols = ["FLOORAUSDT", "FLOORBUSDT", "FLOORCUSDT", "REALEARLYUSDT"]
    _write_manifest(fake_universe["manifest"], symbols)
    monkeypatch.setattr(im_mod, "fetch_exchange_info", lambda: {
        s: {
            "base": s[:-4], "quote": "USDT", "status": "TRADING", "contract_type": "PERPETUAL",
            "onboard_ts": TS("2024-01-01"),
            "tick_size": 0.01, "step_size": 1.0, "min_notional": 5.0,
        }
        for s in symbols
    })
    for s in ("FLOORAUSDT", "FLOORBUSDT", "FLOORCUSDT"):
        _write_oi(fake_universe["oi"], s, "2021-12-01 00:00", "2021-12-05")
    _write_oi(fake_universe["oi"], "REALEARLYUSDT", "2023-06-01 00:00", "2023-06-05")

    out = im_mod.build()
    floor_rows = out.loc[out["symbol"].isin(["FLOORAUSDT", "FLOORBUSDT", "FLOORCUSDT"])]
    assert (floor_rows["listing_ts"] == TS("2024-01-01")).all()
    assert (floor_rows["listing_ts_source"] == "exchangeinfo_onboard_ts").all()
    assert (floor_rows["first_oi_ts"] == TS("2021-12-01")).all()

    real_row = out.loc[out["symbol"] == "REALEARLYUSDT"].iloc[0]
    assert real_row["listing_ts"] == TS("2023-06-01")
    assert real_row["listing_ts_source"] == "first_oi_ts"


def test_detect_source_floor_requires_minimum_symbol_count():
    assert im_mod._detect_source_floor([TS("2021-01-01"), TS("2021-01-01")]) is None
    assert im_mod._detect_source_floor([TS("2021-01-01")] * 3) == TS("2021-01-01")
    assert im_mod._detect_source_floor([]) is None


def test_perp_kline_bounds_unions_v2_and_legacy_sources(fake_universe):
    # legacy panel proves an earlier start than the (partial) v2 pipeline
    year_dir = fake_universe["perp"] / "symbol=UNIOUSDT" / "year=2024"
    year_dir.mkdir(parents=True)
    idx_v2 = pd.date_range("2024-06-01", periods=10, freq="5min", tz="UTC")
    pd.DataFrame({"timestamp": idx_v2, "close": 1.0}).to_parquet(year_dir / "perp_5m.parquet", index=False)

    idx_legacy = pd.date_range("2023-01-01", periods=10, freq="1D", tz="UTC")
    pd.DataFrame({"open_time": idx_legacy, "close": 1.0}).to_parquet(
        fake_universe["klines_1d"] / "UNIOUSDT_1d.parquet", index=False
    )

    bounds = im_mod.perp_kline_bounds("UNIOUSDT")
    assert bounds[0] == idx_legacy.min()  # earlier of the two wins
    assert bounds[1] == idx_v2.max()      # later of the two wins
