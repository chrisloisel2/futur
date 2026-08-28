#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.provenance import audit_feature_provenance, load_feature_provenance_manifest
from alpha_foundry_v5.quality import audit_point_in_time, require_pit_clean
from alpha_foundry_v5.support_audit import DEFAULT_LABS, run_mechanism_support_audit
from alpha_foundry_v5.support_io import load_projected_support_frame
from alpha_foundry_v5.support_stream import run_streaming_mechanism_support_audit


def _digest(payload) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _csv(value: str):
    return [x.strip().upper() for x in str(value).split(",") if x.strip()]


def _progress(message: str) -> None:
    print("[alpha-foundry-v5/support] %s" % message, file=sys.stderr, flush=True)


def _small_file_audit(data: str, selected, registry, provenance):
    """Compatibility path for CSV/single-parquet inputs.

    Production multimodal tensor directories must use the streaming path below;
    this fallback deliberately remains limited to small standalone artifacts.
    """
    frame, load_report = load_projected_support_frame(data, selected, registry)
    _progress(
        "small-file mode loaded %d rows x %d columns"
        % (len(frame), len(frame.columns))
    )
    sort_cols = [c for c in ("asof_ns", "symbol") if c in frame.columns]
    if sort_cols:
        frame = frame.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
    pit = audit_point_in_time(frame)
    require_pit_clean(pit)
    provenance_audit = audit_feature_provenance(frame, provenance)
    if not provenance_audit.clean:
        raise ValueError("feature provenance audit failed: %s" % (provenance_audit,))
    readiness = {lab_id: registry.readiness(lab_id, frame) for lab_id in selected}
    audit = run_mechanism_support_audit(frame, readiness, labs=selected)
    audit["feature_provenance_digest"] = provenance_audit.manifest_digest
    audit["pit_proof_level"] = "FULL_FEATURE_PROVENANCE"
    audit["load_report"] = load_report
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Target-free Alpha Foundry V5 mechanism support audit and hypothesis-budget allocation"
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--feature-provenance", help="FEATURE_PROVENANCE.json; inferred from --data directory")
    parser.add_argument("--labs", default=",".join(DEFAULT_LABS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--budget-out")
    args = parser.parse_args()

    selected = _csv(args.labs)
    registry = LabRegistry()
    provenance = load_feature_provenance_manifest(args.feature_provenance or args.data)
    if provenance is None:
        raise ValueError("support audit requires FEATURE_PROVENANCE.json")

    data_path = Path(args.data)
    if data_path.is_dir():
        _progress(
            "STREAMING MODE: no full-frame concat; labs=%s"
            % ",".join(selected)
        )
        audit = run_streaming_mechanism_support_audit(
            args.data,
            provenance,
            registry,
            labs=selected,
            progress=_progress,
        )
    else:
        _progress("small standalone input; using compatibility full-frame path")
        audit = _small_file_audit(args.data, selected, registry, provenance)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")

    budgets = {
        "version": 1,
        "support_audit_digest": audit["audit_digest"],
        "support_policy_digest": audit["policy_digest"],
        "feature_provenance_digest": audit["feature_provenance_digest"],
        "target_free": True,
        "labs": {
            lab_id: {
                "support_verdict": row["support_verdict"],
                "max_hypothesis_tests": int(row["recommended_max_hypothesis_tests"]),
            }
            for lab_id, row in sorted(audit["labs"].items())
        },
        "policy": "hypothesis budget is allocated before any target/IC scan; zero-budget mechanisms may not enter discovery",
    }
    budgets["manifest_digest"] = _digest(budgets)
    budget_path = Path(args.budget_out) if args.budget_out else out.with_name("HYPOTHESIS_BUDGETS.json")
    budget_path.parent.mkdir(parents=True, exist_ok=True)
    budget_path.write_text(json.dumps(budgets, indent=2, sort_keys=True), encoding="utf-8")

    _progress("wrote support audit and hypothesis budget manifest")
    print(json.dumps({
        "support_audit": str(out),
        "hypothesis_budgets": str(budget_path),
        "strong_support_labs": audit["strong_support_labs"],
        "adequate_support_labs": audit["adequate_support_labs"],
        "thin_or_blocked_labs": audit["thin_or_blocked_labs"],
        "budgets": {k: v["max_hypothesis_tests"] for k, v in budgets["labs"].items()},
        "feature_provenance_digest": audit["feature_provenance_digest"],
        "load_report": audit.get("load_report", {}),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
