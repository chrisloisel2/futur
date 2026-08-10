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

from scripts.build_data_v2_readiness import _load_oi, _load_year_partitioned, VISION_OI_FLOOR  # noqa: E402

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_PATH = ROOT / "reports/BACKFILL_BOUND_REPAIR.json"

# same grace period InstrumentMaster V2 and data_v2.validation.validator
# both use for listing/delisting boundary slop -- a gap this small or
# smaller is normal jitter between sources, not a real repair candidate.
GAP_TOLERANCE = pd.Timedelta(hours=24)

# source_available_from mirrors build_data_v2_readiness.py's own
# DATASET_SPECS: the earliest instant the SOURCE itself can possibly have
# data, independent of any symbol's listing_ts. Only oi_vision_5m has a
# documented one (VISION_OI_FLOOR, Binance Vision futures metrics'
# archive-wide floor) -- without capping the repair target at it, a symbol
# listed before 2020-09-01 (e.g. ETHUSDT, 2019-11-27) would get flagged for
# a "gap" that is partly impossible to fill (no OI archive exists before
# the floor, no amount of delta-backfilling changes that).
DATASETS = {
    "oi_vision_5m": dict(loader=_load_oi, ts_col="create_time", source_available_from=VISION_OI_FLOOR),
    "perp_5m": dict(
        loader=lambda s: _load_year_partitioned(ROOT / "data_v2/normalized/perp_ohlcv/venue=binance", s, "perp_5m.parquet"),
        ts_col="timestamp", source_available_from=None,
    ),
    "spot_5m": dict(
        loader=lambda s: _load_year_partitioned(ROOT / "data_v2/normalized/spot_ohlcv/venue=binance", s, "spot_5m.parquet"),
        ts_col="timestamp", source_available_from=None,
    ),
    "agg_trades_flow_5m": dict(
        loader=lambda s: _load_year_partitioned(ROOT / "data_v2/normalized/agg_trades_flow/5m/venue=binance", s, "flow.parquet"),
        ts_col="timestamp", source_available_from=None,
    ),
}


def build(im: Optional[pd.DataFrame] = None) -> dict:
    im = im if im is not None else pd.read_parquet(INSTRUMENT_MASTER)
    repairs = []
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
            if gap > GAP_TOLERANCE:
                repairs.append({
                    "symbol": symbol,
                    "dataset": dataset,
                    "canonical_listing_ts": str(canonical_listing_ts),
                    "repair_target_start": str(repair_target),
                    "listing_ts_source": row["listing_ts_source"],
                    "metadata_conflict": bool(row["metadata_conflict"]),
                    "observed_start_on_disk": str(observed_start),
                    "gap_days": round(gap.total_seconds() / 86400, 2),
                    "action": "delta_backfill_earlier_window",
                })

    repairs.sort(key=lambda r: (-r["gap_days"], r["symbol"], r["dataset"]))
    by_dataset = {}
    for dataset in DATASETS:
        entries = [r for r in repairs if r["dataset"] == dataset]
        by_dataset[dataset] = {
            "symbols_needing_delta_backfill": len(entries),
            "total_gap_days": round(sum(r["gap_days"] for r in entries), 1),
        }

    return {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "instrument_master": str(INSTRUMENT_MASTER),
        "gap_tolerance_hours": GAP_TOLERANCE.total_seconds() / 3600,
        "pairs_checked": n_checked,
        "repairs_needed": len(repairs),
        "by_dataset": by_dataset,
        "repairs": repairs,
    }


def main() -> None:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))

    print(f"Checked {out['pairs_checked']} (symbol, dataset) pairs with data already on disk.")
    print(f"{out['repairs_needed']} need a delta backfill for a newly-revealed earlier window:")
    for dataset, s in out["by_dataset"].items():
        print(f"  {dataset:22} {s['symbols_needing_delta_backfill']:3} symbols, {s['total_gap_days']:8.1f} total gap-days")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
