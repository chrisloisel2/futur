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
from alpha_foundry_v5.hypothesis_budget import (
    HypothesisBudgetLedger,
    load_hypothesis_budget_manifest,
    require_lab_budget,
)
from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.ledger import SearchLedger
from alpha_foundry_v5.lineage import ExperimentRegistry
from alpha_foundry_v5.manifest import load_manifest, verify_manifest
from alpha_foundry_v5.multiplicity import FamilyTestLedger
from alpha_foundry_v5.provenance import audit_feature_provenance, load_feature_provenance_manifest
from alpha_foundry_v5.quality import audit_point_in_time, require_pit_clean
from alpha_foundry_v5.repro import verify_code_commit
from alpha_foundry_v5.research_engine import ResearchEngine, build_evidence
from alpha_foundry_v5.support_io import parquet_union_schema, support_projection_columns
from alpha_foundry_v5.validation import DEFAULT_POLICY, ValidationEngine


def load_frame(path: str, lab: str, target_name: str, feature_columns: tuple[str, ...]):
    p = Path(path)

    if p.suffix.lower() == ".csv":
        frame = pd.read_csv(p)
        return frame, {
            "mode": "csv_full",
            "parts": 1,
            "logical_columns": len(frame.columns),
            "loaded_columns": len(frame.columns),
        }

    if p.is_file():
        frame = pd.read_parquet(p)
        return frame, {
            "mode": "single_parquet_full",
            "parts": 1,
            "logical_columns": len(frame.columns),
            "loaded_columns": len(frame.columns),
        }

    registry = LabRegistry()
    parts, all_columns, by_part = parquet_union_schema(str(p))

    # Structural/audit columns (support_projection_columns) plus the frozen, explicit
    # feature_columns for this hypothesis -- NOT recomputed from a plugin heuristic here.
    # See alpha_foundry_v5/feature_sets.py and P0-3 in docs/.
    selected = set(
        support_projection_columns(
            all_columns,
            [lab],
            registry,
        )
    )
    unknown = [c for c in feature_columns if c not in all_columns]
    if unknown:
        raise ValueError(f"frozen feature set references columns absent from --data: {unknown}")
    selected.update(feature_columns)

    if "price_fair_value" in all_columns:
        selected.add("price_fair_value")

    if target_name == "loo_fair_value_return":
        for column in all_columns:
            name = str(column)
            if (
                name.endswith("__price_mid")
                or name.endswith("__price_weight")
            ):
                selected.add(name)

    elif target_name == "basis_convergence":
        if "basis_bps" in all_columns:
            selected.add("basis_bps")

    elif target_name == "post_fill_markout":
        if "exec__post_fill_markout_bps" in all_columns:
            selected.add("exec__post_fill_markout_bps")

    projection = [
        column
        for column in all_columns
        if column in selected
    ]

    if "asof_ns" not in projection:
        raise ValueError("asof_ns missing from discovery projection")

    if "symbol" not in projection:
        raise ValueError("symbol missing from discovery projection")

    chunks = []

    for part in parts:
        available = set(by_part[str(part)])

        columns = [
            column
            for column in projection
            if column in available
        ]

        chunks.append(
            pd.read_parquet(
                part,
                columns=columns,
            )
        )

    frame = pd.concat(
        chunks,
        ignore_index=True,
        sort=False,
    )

    frame = frame.reindex(
        columns=projection
    )

    report = {
        "mode": "parquet_discovery_projection",
        "parts": len(parts),
        "logical_columns": len(all_columns),
        "loaded_columns": len(projection),
        "pruned_columns": len(all_columns) - len(projection),
        "target_name": target_name,
        "lab": lab,
    }

    return frame, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one preregistered Alpha Foundry V5 discovery hypothesis")
    parser.add_argument("--data", required=True)
    parser.add_argument("--feature-provenance", help="FEATURE_PROVENANCE.json; inferred from --data when it is a tensor directory")
    parser.add_argument("--support-budget-manifest", required=True, help="Target-free HYPOTHESIS_BUDGETS.json emitted by support audit")
    parser.add_argument("--dataset-manifest", required=True, help="Frozen DatasetManifest JSON written by alpha_foundry_v5_freeze_dataset.py")
    parser.add_argument("--dataset-manifest-digest", required=True, help="Must equal the loaded --dataset-manifest's own digest -- checked, not trusted")
    parser.add_argument("--lab", required=True)
    parser.add_argument("--feature-set", required=True, help="Frozen FeatureSet JSON written by alpha_foundry_v5_freeze_feature_set.py")
    parser.add_argument("--feature-set-id", required=True, help="Must equal the loaded --feature-set's own feature_set_id -- checked, not trusted")
    parser.add_argument("--target")
    parser.add_argument("--horizon-ms", required=True, type=int)
    parser.add_argument("--expected-sign", type=int, choices=[-1, 1], default=1)
    parser.add_argument("--cadence-ms", required=True, type=int)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--ledger", default="reports/alpha_foundry_v5/SEARCH_LEDGER.jsonl")
    parser.add_argument("--hypothesis-budget-ledger", default="reports/alpha_foundry_v5/HYPOTHESIS_BUDGET_LEDGER.jsonl")
    parser.add_argument("--multiplicity-ledger", default="reports/alpha_foundry_v5/MULTIPLICITY_LEDGER.jsonl")
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

    registry = LabRegistry()
    target_name = args.target or registry.spec(args.lab).default_target
    frame, load_report = load_frame(
        args.data,
        args.lab,
        target_name,
        feature_set.columns,
    )
    load_report["feature_set_id"] = feature_set.feature_set_id
    load_report["feature_set_digest"] = feature_set.digest
    load_report["n_feature_columns"] = len(feature_set.columns)
    print(
        "[alpha-foundry-v5/discovery] loader="
        + json.dumps(load_report, sort_keys=True)
    )
    sort_cols = [c for c in ("asof_ns", "symbol") if c in frame.columns]
    if sort_cols:
        frame = frame.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
    require_pit_clean(audit_point_in_time(frame))

    provenance = load_feature_provenance_manifest(args.feature_provenance or args.data)
    if provenance is None:
        raise ValueError("discovery requires FEATURE_PROVENANCE.json; seal the tensor provenance first")
    provenance_audit = audit_feature_provenance(frame, provenance)
    if not provenance_audit.clean:
        raise ValueError("feature provenance audit failed: %s" % (provenance_audit,))

    budget_manifest = load_hypothesis_budget_manifest(args.support_budget_manifest)
    budget_manifest_digest = str(budget_manifest["manifest_digest"])
    max_hypothesis_tests = require_lab_budget(
        budget_manifest,
        args.lab,
        provenance_audit.manifest_digest,
    )

    hypothesis = hypothesis_grid(
        args.lab,
        args.feature_set_id,
        target_name=args.target,
        horizons_ms=[args.horizon_ms],
        expected_sign=args.expected_sign,
    )[0]
    start_ns = int(pd.to_numeric(frame["asof_ns"]).min())
    stop_ns = int(pd.to_numeric(frame["asof_ns"]).max()) + 1
    experiment = ExperimentSpec(
        experiment_id=args.experiment_id,
        hypothesis_digest=hypothesis.digest,
        stage=ResearchStage.DEV_DISCOVERY,
        dataset_manifest_digest=args.dataset_manifest_digest,
        window=TimeWindow(start_ns, stop_ns),
        code_commit=args.code_commit,
        seed=17,
        label_horizon_ms=args.horizon_ms,
        lookback_ms=hypothesis.max_lookback_ms,
        search_family_id=hypothesis.family_id,
    )

    # Registration is side-effect-free with respect to statistical computation.
    # Final-hypothesis budget is then RESERVED before model search starts.
    registry = ExperimentRegistry(args.experiment_registry)
    registry.register(experiment)
    budget_ledger = HypothesisBudgetLedger(args.hypothesis_budget_ledger)
    reservation = budget_ledger.reserve(
        lab_id=hypothesis.lab_id,
        family_id=hypothesis.family_id,
        hypothesis_digest=hypothesis.digest,
        experiment_digest=experiment.digest,
        budget_manifest_digest=budget_manifest_digest,
        max_hypothesis_tests=max_hypothesis_tests,
    )

    configs = [{"alpha": x} for x in (args.ridge_alpha or [0.01, 0.1, 1.0, 10.0])]
    engine = ResearchEngine(SearchLedger(args.ledger))
    result = engine.run_discovery(frame, hypothesis, experiment, args.cadence_ms, configs=configs)

    multiplicity = FamilyTestLedger(args.multiplicity_ledger)
    multiplicity.record(hypothesis.family_id, hypothesis.digest, experiment.digest, result.block_p)
    current_q = multiplicity.qvalues(hypothesis.family_id)[experiment.digest]
    multiplicity_state = multiplicity.verify()

    family_records = [r for r in multiplicity.records() if r.family_id == hypothesis.family_id]
    pvalue_family = [r.p_value for r in family_records]
    own_pvalue_index = [r.experiment_digest for r in family_records].index(experiment.digest)
    evidence = build_evidence(
        result,
        stage=ResearchStage.DEV_DISCOVERY,
        pvalue_family=pvalue_family,
        own_pvalue_index=own_pvalue_index,
        primary_symbols=hypothesis.required_primary_symbols,
        discovery_window=experiment.window,
        evaluation_window=experiment.window,
        block_size_rows=3000,
        expected_sign=hypothesis.expected_sign,
    )
    decision = ValidationEngine(DEFAULT_POLICY).statistical_gate(
        ResearchStage.DEV_DISCOVERY, evidence, expected_sign=hypothesis.expected_sign
    )

    completion = budget_ledger.complete(experiment.digest)
    budget_state = budget_ledger.verify()
    budget_used = budget_ledger.used(hypothesis.lab_id, budget_manifest_digest)
    budget_remaining = max(0, int(max_hypothesis_tests) - int(budget_used))

    store = ArtifactStore(args.artifact_root)
    store.write_parquet(
        args.experiment_id,
        "predictions.parquet",
        pd.DataFrame({
            "asof_ns": result.timestamps_ns,
            "prediction": result.prediction,
            "target": result.target,
        }),
    )
    summary = {
        "hypothesis_id": result.hypothesis_id,
        "hypothesis_digest": result.hypothesis_digest,
        "experiment_digest": result.experiment_digest,
        "expected_sign": args.expected_sign,
        "n": result.n,
        "ess": result.ess,
        "ic": result.ic,
        "block_p": result.block_p,
        "q_value_at_run": current_q,
        "multiplicity_family_id": hypothesis.family_id,
        "multiplicity_tests_at_run": multiplicity.test_count(hypothesis.family_id),
        "feature_provenance_digest": provenance_audit.manifest_digest,
        "support_budget_manifest_digest": budget_manifest_digest,
        "support_budget_max_hypothesis_tests": int(max_hypothesis_tests),
        "support_budget_used": int(budget_used),
        "support_budget_remaining": int(budget_remaining),
        "hypothesis_budget_reservation_hash": reservation.record_hash,
        "hypothesis_budget_completion_hash": completion.record_hash,
        "fold_ics": list(result.fold_ics),
        "tried_configs": list(result.tried_configs),
        "statistical_evidence": asdict(evidence),
        "gate_passed": decision.passed,
        "gate_failures": list(decision.failures),
        "gate_stage": decision.stage.value,
    }
    store.write_json(args.experiment_id, "SUMMARY.json", summary)
    seal = store.seal(args.experiment_id, {
        "dataset_manifest_digest": args.dataset_manifest_digest,
        "feature_provenance_digest": provenance_audit.manifest_digest,
        "support_budget_manifest_digest": budget_manifest_digest,
        "hypothesis_budget_ledger_head": budget_state.get("head_hash", ""),
        "code_commit": args.code_commit,
        "multiplicity_ledger_head": multiplicity_state.get("head_hash", ""),
        "hypothesis": asdict(hypothesis),
        "feature_set_digest": feature_set.digest,
        "feature_set_columns": list(feature_set.columns),
    })
    print(json.dumps({"summary": summary, "seal": seal}, indent=2, sort_keys=True))
    return 0 if decision.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
