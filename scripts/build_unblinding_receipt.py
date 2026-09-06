#!/usr/bin/env python3
"""
scripts/build_unblinding_receipt.py
─────────────────────────────────────────────────────────────────────────────
Data V2 Phase 2, section 19: reports/UNBLINDING_RECEIPT.json -- written
AFTER reports/PREUNBLINDING_FREEZE.json, BEFORE scripts/run_event_scanner_v1.py
is ever invoked. Restates the freeze's hashes plus the EXACT command that
will be run and the horizon/cost model version, so the receipt is a
complete, standalone record of "this is what was about to be done" even if
someone only ever sees this file. Refuses to run if the scan has already
produced results, or if the freeze's own git_sha doesn't match HEAD.

    python3 scripts/build_unblinding_receipt.py
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

FREEZE_PATH = ROOT / "reports/PREUNBLINDING_FREEZE.json"
SCANNER_RESULTS = ROOT / "reports/EVENT_SCANNER_V1_RESULTS.json"
COST_MODEL_FILE = ROOT / "data_v2/events/costs.py"
OUT_PATH = ROOT / "reports/UNBLINDING_RECEIPT.json"

EXACT_SCANNER_COMMAND = "/home/qbee/futur/.venv/bin/python3 scripts/run_event_scanner_v1.py"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if SCANNER_RESULTS.exists():
        print(f"FATAL: {SCANNER_RESULTS} already exists -- results already seen, refusing to issue a receipt.")
        sys.exit(1)
    if not FREEZE_PATH.exists():
        print(f"FATAL: {FREEZE_PATH} does not exist -- run build_preunblinding_freeze.py first.")
        sys.exit(1)

    freeze = json.loads(FREEZE_PATH.read_text())
    git_sha = _git_sha()
    if freeze.get("git_sha") != git_sha:
        print(f"FATAL: freeze git_sha={freeze.get('git_sha')} != current HEAD={git_sha} -- code changed since freeze.")
        sys.exit(1)

    out = {
        "freeze_git_sha": freeze.get("git_sha"),
        "current_git_sha": git_sha,
        "timestamp": str(pd.Timestamp.now(tz="UTC")),
        "hashes": {k: v for k, v in freeze.items() if k.endswith("_sha256")},
        "row_count": freeze.get("row_count"),
        "symbol_count": freeze.get("symbol_count"),
        "family_eligible_row_counts": freeze.get("family_eligible_row_counts"),
        "family_eligible_symbol_counts": freeze.get("family_eligible_symbol_counts"),
        "exact_scanner_command": EXACT_SCANNER_COMMAND,
        "primary_horizon": "1h",
        "cost_model_version_sha256": _sha256_file(COST_MODEL_FILE),
        "economic_results_seen_before_run": False,
        "scanner_executed_before_run": False,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"Receipt issued for freeze git_sha={out['freeze_git_sha']}")
    print(f"Exact command to run next: {EXACT_SCANNER_COMMAND}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
