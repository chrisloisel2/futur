from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_foundry_v5.artifacts import ArtifactStore
from alpha_foundry_v5.contracts import ExperimentSpec, ResearchStage, TimeWindow
from alpha_foundry_v5.hypotheses import hypothesis_grid
from alpha_foundry_v5.hypothesis_budget import (
    HypothesisBudgetLedger,
    load_hypothesis_budget_manifest,
    require_lab_budget,
)
from alpha_foundry_v5.ledger import SearchLedger
from alpha_foundry_v5.lineage import ExperimentRegistry
from alpha_foundry_v5.multiplicity import FamilyTestLedger
from alpha_foundry_v5.provenance import audit_feature_provenance, load_feature_provenance_manifest
from alpha_foundry_v5.quality import audit_point_in_time, require_pit_clean
from alpha_foundry_v5.research_engine import ResearchEngine


def load_frame(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p)
    if p.is_dir():
        parts = sorted(p.glob("part-*.parquet"))
        if not parts:
            raise ValueError("no parquet parts")
        return pd.concat([pd.read_parquet(x) for x in parts], ignore_index=True)
    return pd.read_parquet(p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one preregistered Alpha Foundry V5 discovery hypothesis")
    parser.add_argument("--data", required=True)
    parser.add_argument("--feature-provenance", help="FEATURE_PROVENANCE.json; inferred from --data when it is a tensor directory")
    parser.add_argument("--support-budget-manifest", required=True, help="Target-free HYPOTHESIS_BUDGETS.json emitted by support audit")
    parser.add_argument("--dataset-manifest-digest", required=True)
    parser.add_argument("--lab", required=True)
    parser.add_argument("--feature-set-id", required=True)
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

    frame = load_frame(args.data)
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
    }
    store.write_json(args.experiment_id, "SUMMARY.json", summary)
    seal = store.seal(args.experiment_id, {
        "dataset_manifest_digest": args.dataset_manifest_digest,
        "feature_provenance_digest": provenance_audit.manifest_digest,
        "support_budget_manifest_digest": budget_manifest_digest,
        "hypothesis_budget_ledger_head": budget_state.get("head_hash", ""),
        "code_commit": args.code_commit,
        "multiplicity_ledger_head": multiplicity_state.get("head_hash", ""),
    })
    print(json.dumps({"summary": summary, "seal": seal}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
