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

Memory: RVD needs every symbol's panel simultaneously (cross-sectional).
Loaded once, float64->float32 downcast on numeric columns after
validate_schema's presence check (schema.py only checks column
PRESENCE/timestamp dtype, never a numeric column's own values) -- roughly
halves the ~23GB naive estimate for the full 312-symbol panel on this
31GB-RAM host. DELEVERAGING/CROWDING/FORCED_FLOW_REVERSAL reuse the same
loaded dict (already paid for by RVD) rather than a second pass.

    python3 scripts/run_event_scanner_v1.py
"""
from __future__ import annotations

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


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _scan_symbols() -> list[str]:
    return sorted({p.name.split("=", 1)[1] for p in PANEL_DIR.glob("symbol=*") if p.is_dir()})


def _load_symbol_panel(symbol: str) -> pd.DataFrame | None:
    parts = sorted((PANEL_DIR / f"symbol={symbol}").glob("year=*/event_feature_panel_5m.parquet"))
    if not parts:
        return None
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    # downcast AFTER load -- validate_schema (called by every detector) only
    # checks column presence + timestamp/research_available_at dtype, never
    # a numeric column's own precision, so this is safe for every detector
    # threshold (>=1.5, >=0.90, >=2.0, ...), all comfortably above float32
    # epsilon.
    float_cols = df.select_dtypes(include=["float64"]).columns
    df[float_cols] = df[float_cols].astype("float32")
    return df


def _filter_eligible(events: pd.DataFrame, panel: dict, elig_col: str) -> pd.DataFrame:
    """Keep only events whose triggering (symbol, timestamp) row has
    elig_col == True. See protocol amendment round 5, item 16: a no-op for
    DELEVERAGING/CROWDING/FORCED_FLOW_REVERSAL, the real enforcement of
    MIN_CROSS_SECTION_SIZE for RELATIVE_VALUE_DISLOCATION."""
    if events.empty:
        return events
    keep = []
    for symbol, group in events.groupby("symbol"):
        df = panel.get(symbol)
        if df is None or elig_col not in df.columns:
            continue
        elig_by_ts = df.set_index("timestamp")[elig_col]
        mask = group["timestamp"].map(elig_by_ts).fillna(False)
        keep.append(group[mask.to_numpy()])
    return pd.concat(keep, ignore_index=True) if keep else events.iloc[0:0]


def main() -> None:
    git_sha = _git_sha()

    if not FREEZE_PATH.exists():
        print("FATAL: reports/PREUNBLINDING_FREEZE.json does not exist -- freeze before scanning (section 18).")
        sys.exit(1)
    freeze = json.loads(FREEZE_PATH.read_text())
    # NOT a literal git_sha equality check: committing the freeze/receipt
    # JSON files themselves (pure data/report artifacts, zero scanner
    # logic) necessarily advances HEAD past whatever commit the freeze
    # could have recorded (it can't hash a commit that includes itself) --
    # found the hard way, this run's first attempt FATAL'd on exactly that.
    # What must actually not have changed is the scanning logic itself:
    # re-hash it now and compare against the frozen values.
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
    print(f"Loading {len(symbols)} symbol panels...", flush=True)
    panel: dict[str, pd.DataFrame] = {}
    for i, symbol in enumerate(symbols, 1):
        df = _load_symbol_panel(symbol)
        if df is not None and not df.empty:
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                print(f"  SKIP {symbol}: missing required columns {missing}")
                continue
            panel[symbol] = df
        if i % 50 == 0:
            print(f"  loaded {i}/{len(symbols)}", flush=True)
    print(f"Loaded {len(panel)} symbol panels.", flush=True)

    results = {}

    if "DELEVERAGING" in families_to_scan:
        print("Detecting DELEVERAGING...", flush=True)
        events = pd.concat(
            [det.detect_deleveraging(df, symbol=s).events for s, df in panel.items()], ignore_index=True
        )
        events = _filter_eligible(events, panel, FAMILY_DETECTOR["DELEVERAGING"])
        labelled = lbl.label_events_multi_symbol(events, panel, family="DELEVERAGING", tick_size_by_symbol=tick_size_by_symbol)
        results["DELEVERAGING"] = scn.build_family_report(labelled, family="DELEVERAGING")

    if "CROWDING" in families_to_scan:
        print("Detecting CROWDING...", flush=True)
        events = pd.concat(
            [det.detect_crowding(df, symbol=s).events for s, df in panel.items()], ignore_index=True
        )
        events = _filter_eligible(events, panel, FAMILY_DETECTOR["CROWDING"])
        labelled = lbl.label_events_multi_symbol(events, panel, family="CROWDING", tick_size_by_symbol=tick_size_by_symbol)
        results["CROWDING"] = scn.build_family_report(labelled, family="CROWDING")

    if "FORCED_FLOW_REVERSAL" in families_to_scan:
        print("Detecting FORCED_FLOW_REVERSAL...", flush=True)
        events = pd.concat(
            [det.detect_forced_flow_reversal(df, symbol=s).events for s, df in panel.items()], ignore_index=True
        )
        events = _filter_eligible(events, panel, FAMILY_DETECTOR["FORCED_FLOW_REVERSAL"])
        labelled = lbl.label_events_multi_symbol(events, panel, family="FORCED_FLOW_REVERSAL", tick_size_by_symbol=tick_size_by_symbol)
        results["FORCED_FLOW_REVERSAL"] = scn.build_family_report(labelled, family="FORCED_FLOW_REVERSAL")

    if "RELATIVE_VALUE_DISLOCATION" in families_to_scan:
        print("Detecting RELATIVE_VALUE_DISLOCATION (cross-sectional)...", flush=True)
        rvd_set = det.detect_relative_value_dislocation(panel)
        events = _filter_eligible(rvd_set.events, panel, FAMILY_DETECTOR["RELATIVE_VALUE_DISLOCATION"])
        labelled = lbl.label_events_multi_symbol(events, panel, family="RELATIVE_VALUE_DISLOCATION", tick_size_by_symbol=tick_size_by_symbol)
        results["RELATIVE_VALUE_DISLOCATION"] = scn.build_family_report(labelled, family="RELATIVE_VALUE_DISLOCATION")

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
