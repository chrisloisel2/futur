#!/usr/bin/env python3
"""
scripts/run_event_scanner_v1.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, sections 20-21: the ONE-SHOT Event Scanner V1 execution.
Written fresh for this run -- no prior script existed under this name or
any equivalent (checked: no scripts/*scanner*.py, no data_v2/events/scanner.py
__main__ block; confirmed before writing this, per mission section 20's
"confirme d'abord le vrai script, ne devine pas son nom").

Runs the pre-registered detectors (data_v2/events/detectors.py) against the
rebuilt event feature panel, filters detected events to eligible_<family>
(see reports/EVENT_SCANNER_V1_PROTOCOL.md amendment round 5, item 16),
labels them (data_v2/events/labels.py), and classifies each family
(data_v2/events/scanner.py) -- ALL untouched, pre-registered, already-tested
code. This script contains ZERO detection/classification logic of its own;
it only orchestrates loading, filtering, and reporting.

Preconditions checked before running (refuses to run otherwise):
  - reports/PREUNBLINDING_FREEZE.json exists, and its frozen scanner_
    source_sha256/protocol_sha256/cost_model_sha256 match a live re-hash
    right now (not literal git_sha equality -- committing the freeze/
    receipt themselves, pure data, necessarily advances HEAD past what
    the freeze could have recorded about itself)
  - reports/EVENT_PANEL_READINESS.json: EVENT_PANEL_READY == true
  - reports/EVENT_RESEARCH_READINESS.json: at least one family *_DATA_READY

A family whose own <FAMILY>_DATA_READY is False is SKIPPED (not scanned),
reported as such, never silently defaulted to KILL.

Memory (2026-08-15, found the hard way -- the first real run of this
script was OOM-killed by the kernel at ~28GB RSS during RELATIVE_VALUE_
DISLOCATION): this is a pure infrastructure/orchestration fix, zero
detection/threshold/family logic touched, made BEFORE any economic result
was ever produced or seen (the killed run wrote no results file) -- same
standing as the protocol's own rounds 1-4 "found by review, not by seeing
output" amendments; retrying with identical detection logic after fixing
a crash is not a second "first look", it never produced a first look.

  - DELEVERAGING/CROWDING/FORCED_FLOW_REVERSAL (single-symbol families):
    streamed ONE symbol at a time -- load, detect, filter, label, discard
    -- never holding more than one symbol's full panel in memory. The
    previous version pre-loaded all 312 full panels once and reused that
    dict for every family; holding 312 full (28-column) frames
    simultaneously was already ~13-20GB before RVD's own matrices.
  - RELATIVE_VALUE_DISLOCATION (needs every symbol simultaneously --
    genuinely cross-sectional, not streamable): loaded via
    _load_lean_rvd_panel, which keeps REAL data only for the 8 columns
    RVD's detector and labels.py actually read (timestamp,
    research_available_at, open, residual_logret_5m, residual_return_1h,
    basis_z_1d, signed_volume, eligible_rvd_base/eligible_rvd) and fills
    every other REQUIRED_COLUMNS entry with an np.broadcast_to READ-ONLY
    VIEW of a single scalar (stride-0 -- costs ~the size of one scalar,
    not one array per symbol) purely to satisfy validate_schema's column-
    PRESENCE check, which schema.py confirms never inspects those
    columns' actual values. detect_relative_value_dislocation itself is
    verified (by reading its source) to never touch those columns either.

    python3 scripts/run_event_scanner_v1.py
"""
from __future__ import annotations

import gc
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_v2.events import detectors as det  # noqa: E402
from data_v2.events import labels as lbl  # noqa: E402
from data_v2.events import scanner as scn  # noqa: E402
from data_v2.events.schema import REQUIRED_COLUMNS  # noqa: E402
from scripts.build_preunblinding_freeze import (  # noqa: E402
    COST_MODEL_FILE, PROTOCOL_FILE, SCANNER_SOURCE_FILES, _sha256_file, _sha256_files,
)

PANEL_DIR = ROOT / "data_v2/normalized/event_feature_panel/venue=binance"
INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
FREEZE_PATH = ROOT / "reports/PREUNBLINDING_FREEZE.json"
PANEL_READINESS_PATH = ROOT / "reports/EVENT_PANEL_READINESS.json"
RESEARCH_READINESS_PATH = ROOT / "reports/EVENT_RESEARCH_READINESS.json"
OUT_PATH = ROOT / "reports/EVENT_SCANNER_V1_RESULTS.json"

FAMILY_DETECTOR = {
    "DELEVERAGING": "eligible_deleveraging",
    "CROWDING": "eligible_crowding",
    "RELATIVE_VALUE_DISLOCATION": "eligible_rvd",
    "FORCED_FLOW_REVERSAL": "eligible_ffr",
}
RESEARCH_READY_KEY = {
    "DELEVERAGING": "DELEVERAGING_DATA_READY",
    "CROWDING": "CROWDING_DATA_READY",
    "RELATIVE_VALUE_DISLOCATION": "RVD_DATA_READY",
    "FORCED_FLOW_REVERSAL": "FFR_DATA_READY",
}
SINGLE_SYMBOL_DETECTORS = {
    "DELEVERAGING": det.detect_deleveraging,
    "CROWDING": det.detect_crowding,
    "FORCED_FLOW_REVERSAL": det.detect_forced_flow_reversal,
}

# columns RVD's own detector (data_v2/events/detectors.py::
# detect_relative_value_dislocation) and labels.py::label_events actually
# read the VALUES of -- verified by reading both functions' source before
# writing this. Every other REQUIRED_COLUMNS entry is schema-presence-only
# for RVD's purposes.
RVD_REAL_COLUMNS = {
    "timestamp", "research_available_at", "open", "residual_logret_5m",
    "residual_return_1h", "basis_z_1d", "signed_volume", "symbol",
}


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _scan_symbols() -> list[str]:
    return sorted({p.name.split("=", 1)[1] for p in PANEL_DIR.glob("symbol=*") if p.is_dir()})


def _load_symbol_panel(symbol: str, *, columns: list[str] | None = None) -> pd.DataFrame | None:
    parts = sorted((PANEL_DIR / f"symbol={symbol}").glob("year=*/event_feature_panel_5m.parquet"))
    if not parts:
        return None
    df = pd.concat([pd.read_parquet(p, columns=columns) for p in parts], ignore_index=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    # downcast AFTER load -- validate_schema (called by every detector) only
    # checks column presence + timestamp/research_available_at dtype, never
    # a numeric column's own precision, so this is safe for every detector
    # threshold (>=1.5, >=0.90, >=2.0, ...), all comfortably above float32
    # epsilon.
    float_cols = df.select_dtypes(include=["float64"]).columns
    df[float_cols] = df[float_cols].astype("float32")
    return df


def _load_lean_rvd_panel(symbol: str) -> pd.DataFrame | None:
    """Real data for RVD_REAL_COLUMNS plus eligible_rvd_base/eligible_rvd
    (needed by the caller's own post-detection filter, not by the detector
    itself); every other REQUIRED_COLUMNS entry filled with a genuinely
    near-zero-memory placeholder purely so validate_schema's presence
    check passes -- verified by reading both detect_relative_value_
    dislocation and label_events' source that neither ever reads these
    columns' actual values.

    Bug found 2026-08-15 testing this against real data before the retry:
    an earlier version used np.broadcast_to (a stride-0, zero-copy numpy
    view) hoping to share one scalar's memory across all n rows -- but
    `df[col] = broadcast_view` triggers pandas' own column-assignment
    copy, silently materializing a full n-length array anyway (verified:
    strides became (4,), not (0,); 13 "free" placeholder columns still
    cost ~36MB per symbol on real BTCUSDT data). Fixed with a genuine
    constant-value representation instead: SparseArray with fill_value
    covering literally 100% of the column costs O(1) memory (measured:
    128 bytes total, any n) rather than O(n). `symbol` (the one required
    string column) uses Categorical instead of Sparse (repeated identical
    string, not a float) -- 692KB instead of object dtype's 44MB on the
    same real symbol, a ~64x reduction."""
    cols = sorted(RVD_REAL_COLUMNS - {"symbol"} | {"eligible_rvd_base", "eligible_rvd"})
    df = _load_symbol_panel(symbol, columns=cols)
    if df is None or df.empty:
        return df
    n = len(df)
    df["symbol"] = pd.Categorical([symbol] * n)
    for col in REQUIRED_COLUMNS:
        if col in df.columns:
            continue
        if col in ("liq_feed_available", "funding_is_settlement"):
            df[col] = pd.arrays.SparseArray(np.zeros(n, dtype="bool"), fill_value=False)
        else:
            df[col] = pd.arrays.SparseArray(np.zeros(n, dtype="float32"), fill_value=np.float32(0.0))
    return df


def _filter_eligible_one_symbol(events: pd.DataFrame, df: pd.DataFrame, elig_col: str) -> pd.DataFrame:
    """Single-symbol version of the eligibility post-filter (see protocol
    amendment round 5, item 16) -- df is already known to be this event
    set's own symbol's frame, no groupby/lookup across symbols needed."""
    if events.empty or elig_col not in df.columns:
        return events.iloc[0:0] if elig_col not in df.columns else events
    elig_by_ts = df.set_index("timestamp")[elig_col]
    mask = events["timestamp"].map(elig_by_ts).fillna(False)
    return events[mask.to_numpy()]


def main() -> None:
    git_sha = _git_sha()

    if not FREEZE_PATH.exists():
        print("FATAL: reports/PREUNBLINDING_FREEZE.json does not exist -- freeze before scanning (section 18).")
        sys.exit(1)
    freeze = json.loads(FREEZE_PATH.read_text())
    # NOT a literal git_sha equality check: committing the freeze/receipt
    # JSON files themselves (pure data/report artifacts, zero scanner
    # logic) necessarily advances HEAD past whatever commit the freeze
    # could have recorded about itself. What must actually not have
    # changed is the scanning logic itself: re-hash it now and compare.
    live_hashes = {
        "scanner_source_sha256": _sha256_files(SCANNER_SOURCE_FILES),
        "protocol_sha256": _sha256_file(PROTOCOL_FILE),
        "cost_model_sha256": _sha256_file(COST_MODEL_FILE),
    }
    for key, live in live_hashes.items():
        frozen = freeze.get(key)
        if frozen != live:
            print(f"FATAL: {key} changed since freeze (frozen={frozen}, live={live}) -- refusing to scan.")
            sys.exit(1)
    if freeze.get("scanner_executed_before_freeze") or freeze.get("economic_results_seen_before_freeze"):
        print("FATAL: freeze record claims economic results were already seen -- refusing a second 'first' run.")
        sys.exit(1)
    if OUT_PATH.exists():
        print(f"FATAL: {OUT_PATH} already exists -- this is a ONE-SHOT scan (mission section 20). "
              f"A second run is not a re-run of V1, it is a new protocol (V2).")
        sys.exit(1)

    panel_readiness = json.loads(PANEL_READINESS_PATH.read_text())
    if not panel_readiness.get("EVENT_PANEL_READY"):
        print("FATAL: EVENT_PANEL_READY is not true -- refusing to scan an unverified panel.")
        sys.exit(1)

    research_readiness = json.loads(RESEARCH_READINESS_PATH.read_text())
    families_to_scan = [
        fam for fam, key in RESEARCH_READY_KEY.items() if research_readiness.get(key)
    ]
    if not families_to_scan:
        print("FATAL: no family has *_DATA_READY == true -- nothing eligible to scan.")
        sys.exit(1)
    print(f"Families to scan (DATA_READY): {families_to_scan}")
    skipped_families = [f for f in FAMILY_DETECTOR if f not in families_to_scan]
    if skipped_families:
        print(f"Families SKIPPED (not DATA_READY): {skipped_families}")

    im = pd.read_parquet(INSTRUMENT_MASTER)
    tick_size_by_symbol = dict(zip(im["symbol"], im["tick_size"]))

    symbols = _scan_symbols()
    results = {}

    single_symbol_families = [f for f in SINGLE_SYMBOL_DETECTORS if f in families_to_scan]
    if single_symbol_families:
        print(f"Streaming {len(symbols)} symbols for {single_symbol_families} "
              f"(one symbol's panel in memory at a time)...", flush=True)
        labelled_accum = {fam: [] for fam in single_symbol_families}
        for i, symbol in enumerate(symbols, 1):
            df = _load_symbol_panel(symbol)
            if df is None or df.empty:
                continue
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                print(f"  SKIP {symbol}: missing required columns {missing}")
                continue
            for fam in single_symbol_families:
                ev = SINGLE_SYMBOL_DETECTORS[fam](df, symbol=symbol).events
                ev = _filter_eligible_one_symbol(ev, df, FAMILY_DETECTOR[fam])
                if ev.empty:
                    continue
                labelled = lbl.label_events(ev, df, family=fam, tick_size=tick_size_by_symbol.get(symbol))
                labelled_accum[fam].append(labelled)
            del df
            if i % 50 == 0:
                gc.collect()
                print(f"  streamed {i}/{len(symbols)}", flush=True)
        for fam in single_symbol_families:
            parts = labelled_accum[fam]
            labelled = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            print(f"Classifying {fam} (N={len(labelled)})...", flush=True)
            results[fam] = scn.build_family_report(labelled, family=fam)
        del labelled_accum
        gc.collect()

    if "RELATIVE_VALUE_DISLOCATION" in families_to_scan:
        print(f"Loading {len(symbols)} lean symbol panels for RELATIVE_VALUE_DISLOCATION "
              f"(real data for {len(RVD_REAL_COLUMNS)} columns only, broadcast placeholders for the rest)...", flush=True)
        rvd_panel: dict[str, pd.DataFrame] = {}
        for i, symbol in enumerate(symbols, 1):
            df = _load_lean_rvd_panel(symbol)
            if df is not None and not df.empty:
                rvd_panel[symbol] = df
            if i % 50 == 0:
                print(f"  loaded {i}/{len(symbols)}", flush=True)
        print(f"Loaded {len(rvd_panel)} lean panels. Detecting RELATIVE_VALUE_DISLOCATION "
              f"(cross-sectional, chunked by calendar year)...", flush=True)
        # Bug found 2026-08-15 (second OOM, dmesg-confirmed: killed at
        # anon-rss=18.3GB, mid-way through detect_relative_value_
        # dislocation itself, not the lean-panel load which by then was
        # already down to ~7GB): the detector's OWN internals build ~12-15
        # SEPARATE full (timestamp x 312-symbol) matrices in the course of
        # one call (residual/basis_z/flow/ra_by_symbol, plus every derived
        # rolling-std/cross-sectional-z/sign/mask/contributed intermediate)
        # -- for the full 2020-2026 span (~692K timestamps) that alone is
        # ~12GB on top of the panel. detect_relative_value_dislocation
        # itself is untouched (frozen, "ne modifie aucune family") --
        # fixed by calling it once PER CALENDAR YEAR instead of once on
        # the full 7-year span, each call given that year's data plus a
        # 35-day lookback (>> the family's own 30d rolling-std window and
        # 12-bar/1h cooldown, so every chunk's target region gets the
        # exact same warmup a single full-span call would have given it --
        # provably equivalent results, not an approximation). Events
        # detected inside the lookback-only prefix are dropped per chunk
        # (already correctly counted as that prefix's OWN chunk's target
        # region). Cuts peak detector-internal memory ~6x (692K -> ~115K
        # timestamps per call).
        LOOKBACK = pd.Timedelta(days=35)
        all_timestamps = pd.concat([df["timestamp"] for df in rvd_panel.values()])
        global_min, global_max = all_timestamps.min(), all_timestamps.max()
        del all_timestamps
        chunk_events = []
        for year in range(global_min.year, global_max.year + 1):
            chunk_start = max(pd.Timestamp(year=year, month=1, day=1, tz="UTC"), global_min)
            chunk_end = min(pd.Timestamp(year=year, month=12, day=31, hour=23, minute=55, tz="UTC"), global_max)
            lookback_start = chunk_start - LOOKBACK
            sliced = {}
            for s, df in rvd_panel.items():
                part = df[(df["timestamp"] >= lookback_start) & (df["timestamp"] <= chunk_end)]
                if not part.empty:
                    sliced[s] = part.reset_index(drop=True)
            if not sliced:
                continue
            print(f"  chunk {year}: {len(sliced)} symbols, "
                  f"{lookback_start.date()}..{chunk_end.date()} ({next(iter(sliced.values())).shape[0]} bars sample)", flush=True)
            chunk_set = det.detect_relative_value_dislocation(sliced)
            if not chunk_set.events.empty:
                chunk_events.append(chunk_set.events[chunk_set.events["timestamp"] >= chunk_start])
            del sliced
            gc.collect()
        rvd_events = pd.concat(chunk_events, ignore_index=True) if chunk_events else pd.DataFrame(
            columns=["timestamp", "research_available_at", "symbol", "family", "trigger_residual_sign"]
        )
        print(f"RELATIVE_VALUE_DISLOCATION raw detections (all chunks): {len(rvd_events)}", flush=True)
        # per-symbol eligibility post-filter (protocol amendment round 5,
        # item 16) -- the real enforcement of MIN_CROSS_SECTION_SIZE, since
        # detect_relative_value_dislocation has no population floor of its own.
        keep = []
        for symbol, group in rvd_events.groupby("symbol"):
            df = rvd_panel.get(symbol)
            if df is None or "eligible_rvd" not in df.columns:
                continue
            elig_by_ts = df.set_index("timestamp")["eligible_rvd"]
            mask = group["timestamp"].map(elig_by_ts).fillna(False)
            keep.append(group[mask.to_numpy()])
        events = pd.concat(keep, ignore_index=True) if keep else rvd_events.iloc[0:0]
        labelled = lbl.label_events_multi_symbol(events, rvd_panel, family="RELATIVE_VALUE_DISLOCATION", tick_size_by_symbol=tick_size_by_symbol)
        print(f"Classifying RELATIVE_VALUE_DISLOCATION (N={len(labelled)})...", flush=True)
        results["RELATIVE_VALUE_DISLOCATION"] = scn.build_family_report(labelled, family="RELATIVE_VALUE_DISLOCATION")
        del rvd_panel
        gc.collect()

    out = {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "git_sha": git_sha,
        "preunblinding_freeze_git_sha": freeze.get("git_sha"),
        "families_scanned": families_to_scan,
        "families_skipped_not_data_ready": skipped_families,
        "primary_classification_horizon": scn.PRIMARY_CLASSIFICATION_HORIZON,
        "families": {fam: report.to_dict() for fam, report in results.items()},
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))

    print("\n=== EVENT SCANNER V1 RESULTS (one-shot) ===")
    for fam, report in results.items():
        h1 = report.by_horizon.get(scn.PRIMARY_CLASSIFICATION_HORIZON)
        if h1:
            print(f"{fam:<28} N={h1.n:>6} gross={h1.gross_expectancy:+.5f} "
                  f"net_x1={h1.net_expectancy_cost_x1:+.5f} net_x2={h1.net_expectancy_cost_x2:+.5f} "
                  f"PF={h1.profit_factor:.2f} WR={h1.win_rate:.2%} -> {report.classification} ({report.classification_reason})")
        else:
            print(f"{fam:<28} -> {report.classification} ({report.classification_reason})")
    print(f"\n-> {OUT_PATH}")


if __name__ == "__main__":
    main()
