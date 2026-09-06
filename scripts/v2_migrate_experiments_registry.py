#!/usr/bin/env python3
"""Phase 0 (V2 master prompt) — build reports/registry/experiments.jsonl.

reports/experiments.yaml already exists (pre-V2, 20-ish entries) but does not
carry the fields the V2 master prompt requires: commit, data_manifest_hash,
config_hash, universe, n_trials (total), costs, seed, links. This script does
NOT invent those fields. It appends each legacy entry to the new append-only
JSONL ledger with the required schema keys present but explicitly null, and
provenance/schema_version markers so nobody mistakes a migrated record for a
fully V2-compliant one.

reports/experiments.yaml is NOT modified or deleted — it stays the
historical source. This script is idempotent: re-running it will not
duplicate experiment_ids already present in the JSONL ledger.

Usage:
    python3 scripts/v2_migrate_experiments_registry.py
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEGACY_YAML = ROOT / "reports" / "experiments.yaml"
REGISTRY = ROOT / "reports" / "registry" / "experiments.jsonl"

REQUIRED_V2_FIELDS = [
    "experiment_id", "hypothesis", "commit", "data_manifest_hash",
    "config_hash", "period", "universe", "n_trials", "costs", "seed",
    "metrics", "verdict", "reason", "artifact_links",
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def existing_ids() -> set[str]:
    if not REGISTRY.exists():
        return set()
    ids = set()
    for line in REGISTRY.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        ids.add(json.loads(line)["experiment_id"])
    return ids


def migrate_legacy_entry(entry: dict) -> dict:
    record = {k: None for k in REQUIRED_V2_FIELDS}
    record["experiment_id"] = entry.get("experiment_id")
    record["hypothesis"] = entry.get("model")
    record["period"] = entry.get("split")
    record["universe"] = entry.get("assets")
    record["metrics"] = {
        "pf_oos": entry.get("pf_oos"),
        "r2_oos": entry.get("r2_oos"),
        "wr_oos": entry.get("wr_oos"),
        "n_trades": entry.get("n_trades"),
        "years": entry.get("years"),
        "features_version": entry.get("features_version"),
    }
    record["verdict"] = entry.get("decision")
    record["reason"] = entry.get("notes") or entry.get("reason")
    record["_migration"] = {
        "source": "reports/experiments.yaml",
        "schema_version": "legacy-pre-v2",
        "original_timestamp": entry.get("timestamp"),
        "note": "commit/data_manifest_hash/config_hash/n_trials/costs/seed/artifact_links "
                "were not tracked in the legacy format and are left null, not fabricated.",
    }
    return record


def main() -> int:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    have = existing_ids()
    legacy = yaml.safe_load(LEGACY_YAML.read_text()) or []
    appended = 0
    with REGISTRY.open("a") as f:
        for entry in legacy:
            exp_id = entry.get("experiment_id")
            if not exp_id or exp_id in have:
                continue
            record = migrate_legacy_entry(entry)
            f.write(json.dumps(record, sort_keys=True) + "\n")
            have.add(exp_id)
            appended += 1
    print(f"appended {appended} migrated record(s) to {REGISTRY.relative_to(ROOT)}")
    print(f"total records now in ledger: {len(have)}")
    print(f"legacy source untouched: {LEGACY_YAML.relative_to(ROOT)} ({len(legacy)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
