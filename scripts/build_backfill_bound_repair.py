#!/usr/bin/env python3
"""
scripts/build_backfill_bound_repair.py
─────────────────────────────────────────────────────────────────────────────
InstrumentMaster V2 → BACKFILL_BOUND_REPAIR.json, the step right after
data_v2/instruments/build_instrument_master.py in the Data V2 rebuild order.

All four P0 backfills key their own start date off instrument_master.
parquet's `listing_ts` at the time they ran (data_v2/normalized/
{perp_ohlcv,spot_ohlcv,agg_trades}/build_*.py and scripts/
extend_binance_metrics_vision_to_pit_universe.py -- all `row.listing_ts`
-bounded, see their own `start = pd.Timestamp(row.listing_ts).date()...`
lines). InstrumentMaster V2's reconciliation can push a symbol's canonical
listing_ts EARLIER than it used to be (a real source -- funding, perp
klines, OI -- proving the market existed before what exchangeInfo's
onboardDate claimed; the AIAUSDT case: ~4 months earlier). Whenever that
happens for a symbol that ALREADY has data on disk for one of the four P0
datasets, that dataset now has a real, provable gap between the new
listing_ts and whatever its own first row already is -- this script finds
exactly those (symbol, dataset) pairs and the size of the gap, so the next
step is a DELTA backfill for just the newly-revealed earlier window, not a
full re-run of everything.

A (symbol, dataset) pair with NO data on disk yet is not a repair
candidate: nothing has been backfilled for it, so it will simply start
from the (already correct, new) listing_ts whenever it eventually runs --
flagging it here would be noise, not an actionable repair.

Round 2 (2026-08-11, after first running the actual delta backfill): a gap
between the canonical listing_ts and a dataset's observed start on disk is
NOT automatically actionable -- the source itself can genuinely have no
data there (confirmed via that backfiller's own manifest `missing_*`
record, i.e. it WAS already attempted and 404'd, not merely "not yet
attempted"). Found on real data: 130/132 oi_vision_5m symbols initially
flagged turned out to have their ENTIRE remaining gap already recorded as
`missing` in Binance Vision's futures metrics endpoint -- e.g. ADAUSDT and
ZRXUSDT both confirmed-404 for all 456 days from 2020-09-01 (the
documented VISION_OI_FLOOR) through 2021-11-30, meaning the metrics
endpoint's TRUE per-symbol floor is later than VISION_OI_FLOOR for most of
the universe, a fact this script cannot know in advance -- only the
backfiller's own manifest can prove it after trying. A gap fully covered
by the relevant manifest's missing-period record is reported separately
under `confirmed_unavailable`, not `repairs` -- it is resolved, just not
fillable, and must not be flagged as still-actionable forever.

Usage:
    /home/qbee/futur/.venv/bin/python3 scripts/build_backfill_bound_repair.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_v2.validation.manifest_gaps import (  # noqa: E402
    DATASET_MANIFEST_SPECS as DATASETS,
    gap_confirmed_unfillable as _gap_confirmed_unfillable,
)

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_PATH = ROOT / "reports/BACKFILL_BOUND_REPAIR.json"

# same grace period InstrumentMaster V2 and data_v2.validation.validator
# both use for listing/delisting boundary slop -- a gap this small or
# smaller is normal jitter between sources, not a real repair candidate.
GAP_TOLERANCE = pd.Timedelta(hours=24)

# DATASETS / _gap_confirmed_unfillable now live in data_v2.validation.
# manifest_gaps -- shared with build_data_v2_readiness.py, which also
# needs to know whether a gap is confirmed-unfillable (to exclude it from
# its own coverage denominator). See that module's docstring for why this
# moved out of here.


def build(im: Optional[pd.DataFrame] = None) -> dict:
    im = im if im is not None else pd.read_parquet(INSTRUMENT_MASTER)
    repairs = []
    confirmed_unavailable = []
    n_checked = 0
    for _, row in im.iterrows():
        symbol = row["symbol"]
        canonical_listing_ts = row["listing_ts"]
        if pd.isna(canonical_listing_ts):
            continue  # unresolved symbol -- nothing to compare against
        canonical_listing_ts = pd.Timestamp(canonical_listing_ts)
        for dataset, spec in DATASETS.items():
            df = spec["loader"](symbol)
            if df is None or df.empty:
                continue  # not backfilled yet -- will start at the right bound whenever it runs
            n_checked += 1
            observed_start = pd.to_datetime(df[spec["ts_col"]], utc=True).min()
            source_floor = spec["source_available_from"]
            repair_target = max(canonical_listing_ts, source_floor) if source_floor is not None else canonical_listing_ts
            gap = observed_start - repair_target
            if gap <= GAP_TOLERANCE:
                continue

            entry = {
                "symbol": symbol,
                "dataset": dataset,
                "canonical_listing_ts": str(canonical_listing_ts),
                "repair_target_start": str(repair_target),
                "listing_ts_source": row["listing_ts_source"],
                "metadata_conflict": bool(row["metadata_conflict"]),
                "observed_start_on_disk": str(observed_start),
                "gap_days": round(gap.total_seconds() / 86400, 2),
            }
            missing = spec["missing_fn"](symbol)
            if _gap_confirmed_unfillable(repair_target, observed_start, missing, spec["granularity"]):
                entry["reason"] = "source confirmed no data for this entire window (backfiller's own manifest)"
                confirmed_unavailable.append(entry)
            else:
                entry["action"] = "delta_backfill_earlier_window"
                repairs.append(entry)

    repairs.sort(key=lambda r: (-r["gap_days"], r["symbol"], r["dataset"]))
    confirmed_unavailable.sort(key=lambda r: (-r["gap_days"], r["symbol"], r["dataset"]))
    by_dataset = {}
    for dataset in DATASETS:
        entries = [r for r in repairs if r["dataset"] == dataset]
        unavailable = [r for r in confirmed_unavailable if r["dataset"] == dataset]
        by_dataset[dataset] = {
            "symbols_needing_delta_backfill": len(entries),
            "total_gap_days": round(sum(r["gap_days"] for r in entries), 1),
            "symbols_confirmed_unavailable": len(unavailable),
        }

    return {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "instrument_master": str(INSTRUMENT_MASTER),
        "gap_tolerance_hours": GAP_TOLERANCE.total_seconds() / 3600,
        "pairs_checked": n_checked,
        "repairs_needed": len(repairs),
        "confirmed_unavailable_count": len(confirmed_unavailable),
        "by_dataset": by_dataset,
        "repairs": repairs,
        "confirmed_unavailable": confirmed_unavailable,
    }


def main() -> None:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))

    print(f"Checked {out['pairs_checked']} (symbol, dataset) pairs with data already on disk.")
    print(f"{out['repairs_needed']} need a delta backfill for a newly-revealed earlier window:")
    for dataset, s in out["by_dataset"].items():
        print(f"  {dataset:22} {s['symbols_needing_delta_backfill']:3} actionable, {s['total_gap_days']:8.1f} gap-days, "
              f"{s['symbols_confirmed_unavailable']:3} confirmed unavailable (not actionable)")
    print(f"{out['confirmed_unavailable_count']} pairs have a gap confirmed unfillable (source 404s the whole window).")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
