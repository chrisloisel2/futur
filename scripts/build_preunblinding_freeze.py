#!/usr/bin/env python3
"""
scripts/build_preunblinding_freeze.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 18: reports/PREUNBLINDING_FREEZE.json -- the last
artifact written before the Event Scanner V1 one-shot run (section 20).
Hashes every upstream report and the scanner's own source (detectors.py,
labels.py, scanner.py, costs.py, the protocol doc) so a later audit can
verify NOTHING changed between this freeze and the actual scan. Refuses to
run if reports/EVENT_SCANNER_V1_RESULTS.json already exists (freezing
after seeing results would defeat the entire point).

    python3 scripts/build_preunblinding_freeze.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INSTRUMENT_MASTER = ROOT / "data_v2/instruments/instrument_master.parquet"
ACQUISITION_FREEZE = ROOT / "reports/DATA_V2_ACQUISITION_FREEZE.json"
DATA_V2_READINESS = ROOT / "reports/DATA_V2_READINESS.json"
EVENT_RESEARCH_READINESS = ROOT / "reports/EVENT_RESEARCH_READINESS.json"
EVENT_PANEL_READINESS = ROOT / "reports/EVENT_PANEL_READINESS.json"
ELIGIBILITY_REPORT = ROOT / "reports/EVENT_FEATURE_ELIGIBILITY_REPORT.json"
BASIS_MANIFEST = ROOT / "reports/BASIS_MANIFEST.json"
EVENT_ELIGIBLE_UNIVERSE = ROOT / "reports/EVENT_ELIGIBLE_UNIVERSE_V1.json"
PANEL_DIR = ROOT / "data_v2/normalized/event_feature_panel/venue=binance"
SCANNER_RESULTS = ROOT / "reports/EVENT_SCANNER_V1_RESULTS.json"

SCANNER_SOURCE_FILES = [
    ROOT / "data_v2/events/detectors.py",
    ROOT / "data_v2/events/labels.py",
    ROOT / "data_v2/events/scanner.py",
    ROOT / "data_v2/events/residuals.py",
    ROOT / "data_v2/events/schema.py",
    ROOT / "data_v2/events/eligibility.py",
    ROOT / "scripts/run_event_scanner_v1.py",
]
PROTOCOL_FILE = ROOT / "reports/EVENT_SCANNER_V1_PROTOCOL.md"
COST_MODEL_FILE = ROOT / "data_v2/events/costs.py"

OUT_PATH = ROOT / "reports/PREUNBLINDING_FREEZE.json"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda p: str(p)):
        h.update(p.read_bytes())
    return h.hexdigest()


def _event_panel_corpus() -> dict:
    files = sorted(PANEL_DIR.glob("symbol=*/year=*/event_feature_panel_5m.parquet"))
    h = hashlib.sha256()
    row_count = 0
    symbols = set()
    min_ts, max_ts = None, None
    family_eligible_rows = {"eligible_deleveraging": 0, "eligible_crowding": 0, "eligible_rvd": 0, "eligible_ffr": 0}
    family_eligible_symbols = {k: set() for k in family_eligible_rows}
    import pyarrow.parquet as pq

    for f in files:
        h.update(f.read_bytes())
        symbol = f.parent.parent.name.split("=", 1)[1]
        symbols.add(symbol)
        # eligible_rvd only exists after build_event_feature_panel.py's
        # compute_cross_sectional_rvd second pass -- request only columns
        # this specific file actually has (all files should match once the
        # full rebuild + second pass complete; this guards against reading
        # a corpus mid-rebuild without a confusing pyarrow stack trace).
        available = set(pq.ParquetFile(f).schema.names)
        cols = ["timestamp"] + [c for c in family_eligible_rows if c in available]
        df = pd.read_parquet(f, columns=cols)
        row_count += len(df)
        if len(df):
            lo, hi = df["timestamp"].min(), df["timestamp"].max()
            min_ts = lo if min_ts is None else min(min_ts, lo)
            max_ts = hi if max_ts is None else max(max_ts, hi)
        for k in family_eligible_rows:
            if k in df.columns:
                n = int(df[k].fillna(False).sum())
                family_eligible_rows[k] += n
                if n:
                    family_eligible_symbols[k].add(symbol)
    return {
        "manifest_sha256": h.hexdigest(),
        "row_count": row_count,
        "symbol_count": len(symbols),
        "min_timestamp": str(min_ts) if min_ts is not None else None,
        "max_timestamp": str(max_ts) if max_ts is not None else None,
        "family_eligible_row_counts": family_eligible_rows,
        "family_eligible_symbol_counts": {k: len(v) for k, v in family_eligible_symbols.items()},
    }


def main() -> None:
    if SCANNER_RESULTS.exists():
        print(f"FATAL: {SCANNER_RESULTS} already exists -- results already seen, refusing to (re)freeze.")
        sys.exit(1)

    for p in (ACQUISITION_FREEZE, DATA_V2_READINESS, EVENT_RESEARCH_READINESS, EVENT_PANEL_READINESS, ELIGIBILITY_REPORT, EVENT_ELIGIBLE_UNIVERSE):
        if not p.exists():
            print(f"FATAL: missing upstream report {p}")
            sys.exit(1)

    print("Hashing event feature panel corpus (this reads every panel file once)...", flush=True)
    panel_corpus = _event_panel_corpus()

    out = {
        "git_sha": _git_sha(),
        "timestamp": str(pd.Timestamp.now(tz="UTC")),
        "instrument_master_sha256": _sha256_file(INSTRUMENT_MASTER),
        "acquisition_freeze_sha256": _sha256_file(ACQUISITION_FREEZE),
        "DATA_V2_READINESS_sha256": _sha256_file(DATA_V2_READINESS),
        "EVENT_RESEARCH_READINESS_sha256": _sha256_file(EVENT_RESEARCH_READINESS),
        "EVENT_PANEL_READINESS_sha256": _sha256_file(EVENT_PANEL_READINESS),
        "EVENT_FEATURE_ELIGIBILITY_REPORT_sha256": _sha256_file(ELIGIBILITY_REPORT),
        "basis_manifest_sha256": _sha256_file(BASIS_MANIFEST),
        "event_panel_manifest_sha256": panel_corpus["manifest_sha256"],
        "EVENT_ELIGIBLE_UNIVERSE_V1_sha256": _sha256_file(EVENT_ELIGIBLE_UNIVERSE),
        "scanner_source_sha256": _sha256_files(SCANNER_SOURCE_FILES),
        "protocol_sha256": _sha256_file(PROTOCOL_FILE),
        "cost_model_sha256": _sha256_file(COST_MODEL_FILE),
        "row_count": panel_corpus["row_count"],
        "symbol_count": panel_corpus["symbol_count"],
        "family_eligible_row_counts": panel_corpus["family_eligible_row_counts"],
        "family_eligible_symbol_counts": panel_corpus["family_eligible_symbol_counts"],
        "min_timestamp": panel_corpus["min_timestamp"],
        "max_timestamp": panel_corpus["max_timestamp"],
        "economic_results_seen_before_freeze": False,
        "scanner_executed_before_freeze": False,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"row_count={out['row_count']} symbol_count={out['symbol_count']}")
    print(f"family_eligible_row_counts={out['family_eligible_row_counts']}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
