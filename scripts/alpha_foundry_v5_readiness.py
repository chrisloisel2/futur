from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.provenance import (
    audit_feature_provenance,
    load_feature_provenance_manifest,
)
from alpha_foundry_v5.quality import audit_point_in_time


def load_frame(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p)
    if p.is_dir():
        parts = sorted(p.glob("part-*.parquet"))
        if not parts:
            raise ValueError("no part-*.parquet under %s" % p)
        return pd.concat([pd.read_parquet(x) for x in parts], ignore_index=True)
    return pd.read_parquet(p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Alpha Foundry V5 lab readiness against a real frame")
    parser.add_argument("--data", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    frame = load_frame(args.data)
    registry = LabRegistry()
    pit = audit_point_in_time(frame)
    manifest = load_feature_provenance_manifest(args.data)
    provenance = audit_feature_provenance(frame, manifest) if manifest is not None else None
    provenance_clean = bool(provenance is not None and provenance.clean)
    if not pit.structural_clean:
        proof_level = "FAILED"
    elif not pit.availability_proved:
        proof_level = "STRUCTURAL_ONLY"
    elif manifest is None:
        proof_level = "CLOCKS_AUDITED_NO_FEATURE_PROVENANCE"
    elif not provenance_clean:
        proof_level = "FEATURE_PROVENANCE_FAILED"
    else:
        proof_level = "FULL_FEATURE_PROVENANCE"
    labs = registry.audit(frame)
    payload = {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "pit_clean": bool(pit.clean and provenance_clean),
        "pit_clock_clean": bool(pit.clean),
        "pit_structural_clean": bool(pit.structural_clean),
        "pit_availability_proved": bool(pit.availability_proved),
        "pit_feature_provenance_proved": provenance_clean,
        "pit_proof_level": proof_level,
        "pit": pit.__dict__,
        "feature_provenance": None if provenance is None else provenance.__dict__,
        "ready_labs": [k for k, v in labs.items() if v["ready"]],
        "blocked_labs": [k for k, v in labs.items() if not v["ready"]],
        "labs": labs,
    }
    text = json.dumps(payload, indent=2, sort_keys=True, default=list)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
