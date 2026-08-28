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
from alpha_foundry_v5.labs.registry import LabRegistry
from alpha_foundry_v5.support_io import parquet_union_schema, support_projection_columns


def load_frame(path: str, lab: str, target_name: str):
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

    selected = set(
        support_projection_columns(
            all_columns,
            [lab],
            registry,
        )
    )

    spec = registry.spec(lab)
    plugin = spec.plugin

    event_tokens = (
        "signed_notional",
        "flow_imbalance",
        "cvd",
        "absorption",
        "interarrival_cv",
        "trades_per_second",
        "flow_acceleration",
        "flow_jerk",
        "ofi",
        "queue_imbalance",
        "cancel",
        "remove",
        "queue_pressure",
        "replenishment",
        "depletion",
        "book_event_intensity",
    )

    for column in all_columns:
        name = str(column)
        lower = name.lower()

        if plugin == "cross_venue":
            if (
                name.endswith("__price_dislocation_bps")
                or name.endswith("__dislocation_bps")
                or name.endswith("__price_mid")
            ):
                selected.add(name)

        elif plugin == "event_microstructure":
            if any(token in lower for token in event_tokens):
                selected.add(name)

        elif plugin == "shock_propagation":
            if any(
                token in lower
                for token in (
                    "spread_bps",
                    "depth_",
                    "notional_to_move",
                    "dispersion_bps",
                )
            ):
                selected.add(name)

        elif plugin == "leverage":
            if any(
                token in lower
                for token in (
                    "open_interest",
                    "funding",
                    "basis",
                    "premium",
                    "liquidation",
                )
            ):
                selected.add(name)

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

    registry = LabRegistry()
    target_name = args.target or registry.spec(args.lab).default_target
    frame, load_report = load_frame(
        args.data,
        args.lab,
        target_name,
    )
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
