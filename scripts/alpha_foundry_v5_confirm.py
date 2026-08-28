#!/usr/bin/env python3
"""Run one INDEPENDENT_CONFIRMATION experiment for a hypothesis that already has a
DEV_DISCOVERY experiment in the registry, on data that must be strictly after (and not
overlap) that discovery window -- ExperimentRegistry.register() enforces this and raises if
it doesn't hold. Mirrors alpha_foundry_v5_discover.py's reproducibility checks exactly; see
that script's load_frame() for the column-projection contract this shares.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.artifacts import ArtifactStore
from alpha_foundry_v5.contracts import ExperimentSpec, ResearchStage, TimeWindow
from alpha_foundry_v5.feature_sets import load_feature_set
from alpha_foundry_v5.hypotheses import hypothesis_grid
from alpha_foundry_v5.ledger import SearchLedger
from alpha_foundry_v5.lineage import ExperimentRegistry
from alpha_foundry_v5.manifest import load_manifest, verify_manifest
from alpha_foundry_v5.multiplicity import FamilyTestLedger
from alpha_foundry_v5.provenance import audit_feature_provenance, load_feature_provenance_manifest
from alpha_foundry_v5.quality import audit_point_in_time, require_pit_clean
from alpha_foundry_v5.repro import verify_code_commit
from alpha_foundry_v5.research_engine import ResearchEngine, build_evidence
from alpha_foundry_v5.validation import DEFAULT_POLICY, ValidationEngine

# alpha_foundry_v5_discover.py owns the real column-projection logic; imported here rather
# than duplicated so both CLIs stay in lockstep with the same frozen-FeatureSet contract.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_foundry_v5_discover import load_frame


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one INDEPENDENT_CONFIRMATION experiment")
    parser.add_argument("--data", required=True, help="Confirmation-window tensor -- must be strictly after and non-overlapping with the discovery window")
    parser.add_argument("--feature-provenance")
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--dataset-manifest-digest", required=True)
    parser.add_argument("--lab", required=True)
    parser.add_argument("--feature-set", required=True)
    parser.add_argument("--feature-set-id", required=True)
    parser.add_argument("--target")
    parser.add_argument("--horizon-ms", required=True, type=int)
    parser.add_argument("--expected-sign", type=int, choices=[-1, 1], default=1)
    parser.add_argument("--cadence-ms", required=True, type=int)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--ledger", default="reports/alpha_foundry_v5/SEARCH_LEDGER.jsonl")
    parser.add_argument("--confirmation-multiplicity-ledger", default="reports/alpha_foundry_v5/CONFIRMATION_MULTIPLICITY_LEDGER.jsonl", help="Separate from discovery's family-search ledger -- confirmation is one locked test, not a multiplicity-tested search")
    parser.add_argument("--experiment-registry", default="reports/alpha_foundry_v5/experiments")
    parser.add_argument("--artifact-root", default="reports/alpha_foundry_v5/artifacts")
    parser.add_argument("--ridge-alpha", action="append", type=float, default=[])
    args = parser.parse_args()

    verify_code_commit(args.code_commit, ROOT)

    dataset_manifest = load_manifest(args.dataset_manifest)
    if dataset_manifest.digest != args.dataset_manifest_digest:
        raise ValueError(
            f"--dataset-manifest-digest {args.dataset_manifest_digest!r} does not match "
            f"the loaded manifest's own digest {dataset_manifest.digest!r} ({args.dataset_manifest})"
        )
    manifest_check = verify_manifest(dataset_manifest)
    if not manifest_check["ok"]:
        raise ValueError(f"dataset manifest verification failed: {manifest_check}")

    feature_set = load_feature_set(args.feature_set)
    if feature_set.feature_set_id != args.feature_set_id:
        raise ValueError(
            f"--feature-set-id {args.feature_set_id!r} does not match "
            f"the loaded feature set's own feature_set_id {feature_set.feature_set_id!r} ({args.feature_set})"
        )
    if feature_set.lab_id != args.lab:
        raise ValueError(f"feature set was frozen for lab {feature_set.lab_id!r}, not --lab {args.lab!r}")

    registry = ExperimentRegistry(args.experiment_registry)

    hypothesis = hypothesis_grid(
        args.lab,
        args.feature_set_id,
        target_name=args.target,
        horizons_ms=[args.horizon_ms],
        expected_sign=args.expected_sign,
    )[0]

    prior_discovery = [
        spec for spec in registry.list_specs()
        if spec.stage == ResearchStage.DEV_DISCOVERY and spec.hypothesis_digest == hypothesis.digest
    ]
    if not prior_discovery:
        raise ValueError(
            f"no DEV_DISCOVERY experiment found for hypothesis_digest {hypothesis.digest!r} in "
            f"{args.experiment_registry} -- confirmation requires an existing discovery experiment "
            "for the exact same hypothesis (same lab/target/horizon/feature_set/expected_sign)"
        )
    earliest_discovery = min(prior_discovery, key=lambda s: s.window.start_ns)
    discovery_window = earliest_discovery.window

    frame, load_report = load_frame(args.data, args.lab, hypothesis.target_name, feature_set.columns)
    print("[alpha-foundry-v5/confirm] loader=" + json.dumps(load_report, sort_keys=True))
    sort_cols = [c for c in ("asof_ns", "symbol") if c in frame.columns]
    if sort_cols:
        frame = frame.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
    require_pit_clean(audit_point_in_time(frame))

    provenance = load_feature_provenance_manifest(args.feature_provenance or args.data)
    if provenance is None:
        raise ValueError("confirmation requires FEATURE_PROVENANCE.json; seal the tensor provenance first")
    provenance_audit = audit_feature_provenance(frame, provenance)
    if not provenance_audit.clean:
        raise ValueError(f"feature provenance audit failed: {provenance_audit}")

    start_ns = int(pd.to_numeric(frame["asof_ns"]).min())
    stop_ns = int(pd.to_numeric(frame["asof_ns"]).max()) + 1
    confirmation_window = TimeWindow(start_ns, stop_ns)
    if confirmation_window.duration_s < hypothesis.confirmation_min_hours * 3600.0:
        raise ValueError(
            f"confirmation window is {confirmation_window.duration_s / 3600.0:.2f}h, "
            f"hypothesis requires >= {hypothesis.confirmation_min_hours}h"
        )

    experiment = ExperimentSpec(
        experiment_id=args.experiment_id,
        hypothesis_digest=hypothesis.digest,
        stage=ResearchStage.INDEPENDENT_CONFIRMATION,
        dataset_manifest_digest=args.dataset_manifest_digest,
        window=confirmation_window,
        code_commit=args.code_commit,
        seed=17,
        label_horizon_ms=args.horizon_ms,
        lookback_ms=hypothesis.max_lookback_ms,
        search_family_id=hypothesis.family_id,
    )
    # register() raises if this window overlaps or isn't strictly after the discovery
    # window found above, for any prior DEV_DISCOVERY experiment sharing hypothesis_digest.
    registry.register(experiment)

    configs = [{"alpha": x} for x in (args.ridge_alpha or [0.01, 0.1, 1.0, 10.0])]
    engine = ResearchEngine(SearchLedger(args.ledger))
    result = engine.run_confirmation(frame, hypothesis, experiment, args.cadence_ms, configs=configs)

    confirm_ledger = FamilyTestLedger(args.confirmation_multiplicity_ledger)
    confirm_ledger.record(hypothesis.family_id, hypothesis.digest, experiment.digest, result.block_p)
    family_records = [r for r in confirm_ledger.records() if r.hypothesis_digest == hypothesis.digest]
    pvalue_family = [r.p_value for r in family_records]
    own_pvalue_index = [r.experiment_digest for r in family_records].index(experiment.digest)

    evidence = build_evidence(
        result,
        stage=ResearchStage.INDEPENDENT_CONFIRMATION,
        pvalue_family=pvalue_family,
        own_pvalue_index=own_pvalue_index,
        primary_symbols=hypothesis.required_primary_symbols,
        discovery_window=discovery_window,
        evaluation_window=confirmation_window,
        block_size_rows=3000,
        expected_sign=hypothesis.expected_sign,
    )
    decision = ValidationEngine(DEFAULT_POLICY).statistical_gate(
        ResearchStage.INDEPENDENT_CONFIRMATION, evidence, expected_sign=hypothesis.expected_sign
    )

    store = ArtifactStore(args.artifact_root)
    store.write_json(args.experiment_id, "predictions_summary.json", {
        "n": result.n, "ess": result.ess, "ic": result.ic, "block_p": result.block_p,
        "fold_ics": list(result.fold_ics), "symbol_ics": dict(result.symbol_ics),
    })
    summary = {
        "hypothesis_id": hypothesis.hypothesis_id,
        "hypothesis_digest": hypothesis.digest,
        "experiment_digest": experiment.digest,
        "discovery_window": {"start_ns": discovery_window.start_ns, "stop_ns": discovery_window.stop_ns},
        "confirmation_window": {"start_ns": confirmation_window.start_ns, "stop_ns": confirmation_window.stop_ns},
        "n": result.n, "ess": result.ess, "ic": result.ic, "block_p": result.block_p,
        "fold_ics": list(result.fold_ics),
        "tried_configs": list(result.tried_configs),
        "feature_provenance_digest": provenance_audit.manifest_digest,
        "statistical_evidence": asdict(evidence),
        "gate_passed": decision.passed,
        "gate_failures": list(decision.failures),
        "gate_stage": decision.stage.value,
    }
    store.write_json(args.experiment_id, "SUMMARY.json", summary)
    seal = store.seal(args.experiment_id, {
        "dataset_manifest_digest": args.dataset_manifest_digest,
        "feature_provenance_digest": provenance_audit.manifest_digest,
        "code_commit": args.code_commit,
        "hypothesis": asdict(hypothesis),
        "feature_set_digest": feature_set.digest,
        "feature_set_columns": list(feature_set.columns),
        "discovery_experiment_digest": earliest_discovery.digest,
    })
    print(json.dumps({"summary": summary, "seal": seal}, indent=2, sort_keys=True))
    return 0 if decision.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
