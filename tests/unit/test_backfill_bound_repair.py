"""
tests/unit/test_backfill_bound_repair.py
─────────────────────────────────────────────────────────────────────────────
scripts/build_backfill_bound_repair.py: comparing InstrumentMaster V2's
reconciled listing_ts against what each P0 backfill already has on disk,
to find exactly which (symbol, dataset) pairs need a delta backfill for a
newly-revealed earlier window -- and which ones only LOOK like a gap but
are actually already confirmed unfillable (the backfiller's own manifest
already 404'd every period in the window).

Gate:
    python3 -m pytest tests/unit/test_backfill_bound_repair.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import build_backfill_bound_repair as repair_mod

TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731

NULL_SPEC = dict(loader=lambda s: None, ts_col="timestamp", source_available_from=None,
                  granularity="day", missing_fn=lambda s: set())


def _im_row(symbol: str, listing_ts, listing_ts_source="exchangeinfo_onboard_ts", metadata_conflict=False) -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": symbol, "listing_ts": listing_ts,
        "listing_ts_source": listing_ts_source, "metadata_conflict": metadata_conflict,
    }])


def _only_dataset(monkeypatch, name: str, spec: dict) -> None:
    """Replace DATASETS with just `name` -> spec, all others no-ops --
    isolates each test to exactly the dataset it's exercising. `spec` only
    needs to override the keys the test cares about; NULL_SPEC fills the rest."""
    datasets = {d: dict(NULL_SPEC) for d in repair_mod.DATASETS}
    datasets[name] = {**NULL_SPEC, **spec}
    monkeypatch.setattr(repair_mod, "DATASETS", datasets)


def test_flags_a_real_gap_beyond_tolerance(monkeypatch):
    im = _im_row("FOOUSDT", TS("2022-01-01"))
    perp_data = pd.DataFrame({"timestamp": pd.date_range("2022-06-01", periods=5, freq="5min", tz="UTC")})
    _only_dataset(monkeypatch, "perp_5m", dict(loader=lambda s: perp_data))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 1
    r = out["repairs"][0]
    assert r["symbol"] == "FOOUSDT" and r["dataset"] == "perp_5m"
    assert r["gap_days"] == pytest.approx(151, abs=1)
    assert r["action"] == "delta_backfill_earlier_window"


def test_gap_within_tolerance_is_not_flagged(monkeypatch):
    im = _im_row("FOOUSDT", TS("2022-01-01 00:00"))
    perp_data = pd.DataFrame({"timestamp": pd.date_range("2022-01-01 12:00", periods=5, freq="5min", tz="UTC")})  # 12h gap
    _only_dataset(monkeypatch, "perp_5m", dict(loader=lambda s: perp_data))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 0


def test_no_data_on_disk_is_not_a_repair_candidate(monkeypatch):
    im = _im_row("FOOUSDT", TS("2022-01-01"))
    monkeypatch.setattr(repair_mod, "DATASETS", {d: dict(NULL_SPEC) for d in repair_mod.DATASETS})

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 0
    assert out["pairs_checked"] == 0


def test_unresolved_listing_ts_is_skipped(monkeypatch):
    im = _im_row("FOOUSDT", pd.NaT)
    perp_data = pd.DataFrame({"timestamp": pd.date_range("2022-06-01", periods=5, freq="5min", tz="UTC")})
    _only_dataset(monkeypatch, "perp_5m", dict(loader=lambda s: perp_data))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 0
    assert out["pairs_checked"] == 0


def test_source_available_from_caps_the_repair_target_not_the_full_gap(monkeypatch):
    """The exact bug found on the real corpus: ETHUSDT listed 2019-11-27,
    but Binance's OI Vision archive itself only exists from 2020-09-01
    (VISION_OI_FLOOR) -- the gap [2019-11-27, 2020-09-01) can NEVER be
    filled by any backfill, so the repair target must be capped at the
    source floor, not the symbol's own listing_ts."""
    im = _im_row("ETHUSDT", TS("2019-11-27"))
    oi_data = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    source_floor = TS("2020-09-01")
    _only_dataset(monkeypatch, "oi_vision_5m", dict(loader=lambda s: oi_data, ts_col="create_time", source_available_from=source_floor))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 1
    r = out["repairs"][0]
    assert r["repair_target_start"] == str(source_floor)  # capped at the source floor, not listing_ts
    # gap is from the FLOOR (2020-09-01) to observed (2021-12-01), ~456 days
    # -- NOT from the raw listing_ts (2019-11-27), which would be ~735 days
    assert r["gap_days"] == pytest.approx(456, abs=1)


def test_by_dataset_summary_counts_match_repairs(monkeypatch):
    im = pd.concat([_im_row("AAAUSDT", TS("2022-01-01")), _im_row("BBBUSDT", TS("2022-01-01"))], ignore_index=True)
    a_data = pd.DataFrame({"timestamp": pd.date_range("2022-06-01", periods=3, freq="5min", tz="UTC")})
    _only_dataset(monkeypatch, "perp_5m", dict(loader=lambda s: a_data if s == "AAAUSDT" else None))

    out = repair_mod.build(im)
    assert out["by_dataset"]["perp_5m"]["symbols_needing_delta_backfill"] == 1
    assert out["by_dataset"]["oi_vision_5m"]["symbols_needing_delta_backfill"] == 0


# ── confirmed_unavailable classification (round 2, 2026-08-11) ────────────


def test_gap_fully_covered_by_missing_days_is_confirmed_unavailable_not_a_repair(monkeypatch):
    """The exact real-corpus finding: ADAUSDT/ZRXUSDT's OI gap from
    2020-09-01 to 2021-12-01 is not an unfetched backfill -- Binance Vision
    genuinely 404s every single day in that window, already recorded in
    the OI backfiller's own manifest. Must be reported as confirmed
    unavailable, not as an actionable repair (which would never clear)."""
    im = _im_row("ADAUSDT", TS("2020-09-01"))
    oi_data = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    all_missing_days = {
        d.date().isoformat()
        for d in pd.date_range("2020-09-01", "2021-11-30", freq="1D", tz="UTC")
    }
    _only_dataset(monkeypatch, "oi_vision_5m", dict(
        loader=lambda s: oi_data, ts_col="create_time",
        missing_fn=lambda s: all_missing_days,
    ))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 0
    assert out["confirmed_unavailable_count"] == 1
    entry = out["confirmed_unavailable"][0]
    assert entry["symbol"] == "ADAUSDT"
    assert "reason" in entry
    assert out["by_dataset"]["oi_vision_5m"]["symbols_confirmed_unavailable"] == 1


def test_gap_partially_covered_by_missing_days_still_actionable(monkeypatch):
    """Only SOME of the gap is confirmed-404 -- there is still real,
    never-attempted territory in the window, so this must stay a genuine
    repair candidate, not get swept into confirmed_unavailable."""
    im = _im_row("FOOUSDT", TS("2020-09-01"))
    oi_data = pd.DataFrame({"create_time": pd.date_range("2021-12-01", periods=5, freq="5min", tz="UTC")})
    # only the first 100 days confirmed missing -- the rest of the window
    # (100 more days) was never attempted at all.
    partial_missing = {
        d.date().isoformat()
        for d in pd.date_range("2020-09-01", periods=100, freq="1D", tz="UTC")
    }
    _only_dataset(monkeypatch, "oi_vision_5m", dict(
        loader=lambda s: oi_data, ts_col="create_time",
        missing_fn=lambda s: partial_missing,
    ))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 1
    assert out["confirmed_unavailable_count"] == 0


def test_month_granularity_confirmed_unavailable(monkeypatch):
    """perp_5m/spot_5m track missing at MONTH granularity, not day."""
    im = _im_row("UNFIUSDT", TS("2020-12-28"))
    perp_data = pd.DataFrame({"timestamp": pd.date_range("2021-02-19", periods=5, freq="5min", tz="UTC")})
    _only_dataset(monkeypatch, "perp_5m", dict(
        loader=lambda s: perp_data,
        granularity="month", missing_fn=lambda s: {"2020-12", "2021-01"},
    ))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 0
    assert out["confirmed_unavailable_count"] == 1


def test_no_missing_record_means_not_confirmed_unavailable(monkeypatch):
    """An empty/absent manifest must never be treated as "confirmed" --
    only an EXPLICIT missing record proves the source was actually tried."""
    im = _im_row("FOOUSDT", TS("2022-01-01"))
    perp_data = pd.DataFrame({"timestamp": pd.date_range("2022-06-01", periods=5, freq="5min", tz="UTC")})
    _only_dataset(monkeypatch, "perp_5m", dict(loader=lambda s: perp_data, missing_fn=lambda s: set()))

    out = repair_mod.build(im)
    assert out["repairs_needed"] == 1
    assert out["confirmed_unavailable_count"] == 0
