"""The pipeline. One spec in, one verdict out.

    load spec -> load data -> gate 0 quality -> gate 1 mechanism and latency
    -> costs -> gate 2 cost wall -> decluster -> gate 3 robustness
    -> gate 4 placebo -> gate 5 multiplicity -> gate 6 forward seal
    -> verdict, report, ledger

The gates run in that order on purpose: cost comes before robustness so that a
mechanism under the cost floor is killed before anyone has seen a Sharpe, and
multiplicity comes before promotion so that the bar rises with the number of
hypotheses the family has spent.

A mechanism module lives at ``mechanisms/<id>/run.py`` and exposes::

    def build(spec: MechanismSpec, ctx: RunContext) -> MechanismRun

It reads data and produces decisions.  It does not compute metrics, does not
decide anything, and never imports the legacy tree.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from research_kernel import KERNEL_VERSION
from research_kernel.cost_model import (
    COST_WALL_MULTIPLE,
    cost_sensitivity,
    passes_cost_wall,
)
from research_kernel.data_contracts import DataQualityReport, latency_gate
from research_kernel.declustering import decluster_frame
from research_kernel.forward_seal import find_seal, verify_unchanged
from research_kernel.ledger import RunLedger
from research_kernel.mechanism_spec import MechanismSpec
from research_kernel.multiplicity import MultiplicityLedger, MultiplicityError
from research_kernel.placebo import PlaceboSuite, placebo_gate
from research_kernel.report import (
    render_verdict_md,
    write_json,
    write_parquet,
    write_verdict_md,
)
from research_kernel.validation import (
    compute_metrics,
    min_detectable_edge_bps,
    robustness_gate,
)
from research_kernel.verdict import Verdict, VerdictStatus

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where the mechanisms may read already-produced data from.  Reading files is
#: allowed by rule 1; importing logic from the legacy tree is not.
DEFAULT_DATA_ROOT = Path("/home/qbee/futur/data")


class DataUnavailable(RuntimeError):
    """The mechanism cannot be tested yet.  Not a failure of the hypothesis."""


@dataclass
class RunContext:
    data_root: Path = DEFAULT_DATA_ROOT
    repo_root: Path = REPO_ROOT
    mode: str = "historical"
    now: str = ""
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MechanismRun:
    """What a mechanism module returns: decisions and the data behind them."""

    decisions: pd.DataFrame
    quality: DataQualityReport
    panel: Optional[pd.DataFrame] = None
    trades: Optional[pd.DataFrame] = None
    cross_sectional: bool = False
    measured_latency_ms: Optional[float] = None
    manifests: List[str] = field(default_factory=list)
    notes: str = ""

    REQUIRED_COLUMNS = ("timestamp", "symbol", "side", "gross_bps")

    def validate(self) -> None:
        missing = [c for c in self.REQUIRED_COLUMNS if c not in self.decisions.columns]
        if missing:
            raise DataUnavailable(
                "decisions frame is missing %s" % ", ".join(missing)
            )


# ---------------------------------------------------------------- utilities
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT)] + list(args),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


def _code_state() -> Dict[str, Any]:
    return {
        "code_commit_sha": _git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(_git("status", "--porcelain")),
    }


def load_mechanism_module(mechanism_dir: Path):
    run_py = Path(mechanism_dir) / "run.py"
    if not run_py.exists():
        raise DataUnavailable("no run.py in %s" % mechanism_dir)
    name = "mechanism_%s" % Path(mechanism_dir).name
    spec = importlib.util.spec_from_file_location(name, run_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if not hasattr(module, "build"):
        raise DataUnavailable("%s does not expose build(spec, ctx)" % run_py)
    return module


# ------------------------------------------------------------------- runner
def run_mechanism(
    spec_path: Path,
    ctx: Optional[RunContext] = None,
    results_dir: Optional[Path] = None,
    multiplicity_ledger: Optional[MultiplicityLedger] = None,
    run_ledger: Optional[RunLedger] = None,
    sealed_root: Optional[Path] = None,
    placebo_draws: int = 2000,
) -> Verdict:
    spec_path = Path(spec_path)
    mechanism_dir = spec_path.parent
    spec = MechanismSpec.from_json(spec_path)

    ctx = ctx or RunContext()
    ctx.now = ctx.now or _utcnow()
    results_dir = Path(results_dir or (mechanism_dir / "results"))
    mult = multiplicity_ledger or MultiplicityLedger(
        REPO_ROOT / "reports" / "research_kernel" / "multiplicity_ledger.json"
    )
    runs = run_ledger or RunLedger(
        REPO_ROOT / "reports" / "research_kernel" / "run_ledger.jsonl"
    )
    sealed_root = Path(sealed_root or (REPO_ROOT / "sealed_forwards"))

    rules_hash = spec.rules_hash()
    code = _code_state()
    passed: List[str] = []
    failed: List[str] = []
    warnings: List[str] = []

    # A look at the data is a trial, whether or not it is called a discovery.
    try:
        mult.record_trial(
            family=spec.multiplicity_family,
            mechanism_id=spec.mechanism_id,
            rules_hash=rules_hash,
            n_trials=spec.n_trials(),
            recorded_at=ctx.now,
            note="first run of %s" % spec.mechanism_id,
        )
    except MultiplicityError as exc:
        if "already sealed" not in str(exc):
            raise
    threshold = mult.current_threshold(spec.multiplicity_family)

    def _emit(
        status: VerdictStatus,
        decision: str,
        metrics: Optional[Dict[str, Any]] = None,
        placebos: Optional[List[Dict[str, Any]]] = None,
        cost_rows: Optional[List[Dict[str, Any]]] = None,
        quality: Optional[Dict[str, Any]] = None,
        decisions: Optional[pd.DataFrame] = None,
        trades: Optional[pd.DataFrame] = None,
        manifests: Optional[List[str]] = None,
        seal_id: Optional[str] = None,
    ) -> Verdict:
        m = metrics or {}
        verdict = Verdict(
            mechanism_id=spec.mechanism_id,
            status=status,
            gross_edge_bps=float(m.get("gross_edge_bps", 0.0) or 0.0),
            cost_bps=float(m.get("cost_bps", 0.0) or 0.0),
            net_edge_bps=float(m.get("net_edge_bps", 0.0) or 0.0),
            net_edge_bps_cost_x2=float(m.get("net_edge_bps_cost_x2", 0.0) or 0.0),
            profit_factor=float(m.get("profit_factor", 0.0) or 0.0),
            profit_factor_cost_x2=float(m.get("profit_factor_cost_x2", 0.0) or 0.0),
            sharpe=float(m.get("sharpe", 0.0) or 0.0),
            max_drawdown=float(m.get("max_drawdown_bps", 0.0) or 0.0),
            n_raw=int(m.get("n_raw", 0) or 0),
            n_independent=int(m.get("n_independent", 0) or 0),
            t_stat=float(m.get("t_stat", 0.0) or 0.0),
            placebo_percentile=float(
                min(
                    [p.get("percentile", float("nan")) for p in (placebos or [])]
                    or [float("nan")]
                )
            ),
            multiplicity_adjusted_threshold=float(threshold),
            passed_gates=list(passed),
            failed_gates=list(failed),
            decision=decision,
            multiplicity_family=spec.multiplicity_family,
            rules_hash=rules_hash,
            forward_seal_id=seal_id,
            kernel_version=KERNEL_VERSION,
            code_commit_sha=code["code_commit_sha"],
            created_at=ctx.now,
            data_manifests=list(manifests or []),
            warnings=list(warnings),
        )

        results_dir.mkdir(parents=True, exist_ok=True)
        write_json(verdict.to_dict(), results_dir / "verdict.json")
        write_json(m, results_dir / "metrics.json")
        write_json(placebos or [], results_dir / "placebo.json")
        write_json(cost_rows or [], results_dir / "cost_sensitivity.json")
        if quality is not None:
            write_json(quality, results_dir / "data_quality_report.json")
        if decisions is not None and len(decisions):
            write_parquet(decisions, results_dir / "decisions.parquet")
        if trades is not None and len(trades):
            write_parquet(trades, results_dir / "trades.parquet")

        write_verdict_md(
            render_verdict_md(
                verdict,
                m,
                placebos or [],
                cost_rows or [],
                {
                    "hypothesis": spec.hypothesis,
                    "economic_reason": spec.economic_reason,
                    "multiplicity_family": spec.multiplicity_family,
                    "horizon": spec.horizon,
                    "side_mode": spec.side_mode,
                    "universe": spec.universe,
                },
                quality,
            ),
            results_dir / "verdict.md",
        )

        runs.append(
            {
                "mechanism_id": spec.mechanism_id,
                "rules_hash": rules_hash,
                "status": status.value,
                "family": spec.multiplicity_family,
                "threshold_t": threshold,
                "t_stat_gross": verdict.t_stat,
                "gross_edge_bps": verdict.gross_edge_bps,
                "cost_bps": verdict.cost_bps,
                "net_edge_bps": verdict.net_edge_bps,
                "n_independent": verdict.n_independent,
                "failed_gates": failed,
                "mode": ctx.mode,
                "code_commit_sha": code["code_commit_sha"],
                "working_tree_dirty": code["working_tree_dirty"],
                "results_dir": str(results_dir),
            }
        )
        return verdict

    # -------------------------------------------------- gate 0: data quality
    try:
        module = load_mechanism_module(mechanism_dir)
        run: MechanismRun = module.build(spec, ctx)
        run.validate()
    except DataUnavailable as exc:
        failed.append("gate 0 data: %s" % exc)
        return _emit(VerdictStatus.DATA_BROKEN, "The data does not exist yet: %s" % exc)

    quality = run.quality.to_dict() if run.quality else None
    if run.quality and not run.quality.passed:
        failed.append("gate 0 data quality: %s" % "; ".join(run.quality.violations[:3]))
        return _emit(
            VerdictStatus.DATA_BROKEN,
            "Data quality rejects the run before any result is computed.",
            quality=quality,
            decisions=run.decisions,
            manifests=run.manifests,
        )
    passed.append("gate 0 data quality")
    if run.quality:
        warnings.extend(run.quality.warnings)

    if len(run.decisions) == 0:
        failed.append("gate 0 data: the rule fired zero times")
        return _emit(
            VerdictStatus.DATA_BROKEN,
            "The rule produced no decision over the validation window.",
            quality=quality,
            manifests=run.manifests,
        )

    # ------------------------------------------- gate 1: mechanism + latency
    # The spec-level economic check already ran at load time (banned phrasings,
    # ranges, missing exit rule).  What is left is the latency budget.
    budget_ms = spec.max_data_latency_ms()
    measured = run.measured_latency_ms
    if measured is None and run.quality is not None:
        measured = run.quality.measured_latency_ms
    violation = latency_gate(measured, budget_ms)
    if violation:
        failed.append("gate 1 latency: %s" % violation)
        return _emit(
            VerdictStatus.REJECTED,
            "Unexecutable by construction: %s" % violation,
            quality=quality,
            decisions=run.decisions,
            manifests=run.manifests,
        )
    passed.append(
        "gate 1 mechanism and latency (%.0f ms measured against a %.0f ms budget)"
        % (measured, budget_ms)
    )

    # ------------------------------------------------------- costs, then gate 2
    cost = spec.cost()
    cost_bps = cost.round_trip_bps()
    metrics = compute_metrics(
        run.decisions,
        cost_bps=cost_bps,
        declustering_rule=spec.declustering_rule,
        cross_sectional=run.cross_sectional,
    )
    metrics["cost_model"] = cost.to_dict()
    metrics["multiplicity_threshold_t"] = threshold
    metrics["multiplicity_family_size"] = mult.family_size(spec.multiplicity_family)
    metrics["min_detectable_edge_bps"] = min_detectable_edge_bps(
        metrics.get("se_bps", float("nan")), threshold
    )
    metrics["notes"] = run.notes
    cost_rows = cost_sensitivity(metrics["gross_edge_bps"], cost_bps)

    if not passes_cost_wall(metrics["gross_edge_bps"], cost_bps):
        failed.append(
            "gate 2 cost: gross %.2f bps < %.0f x cost %.2f bps"
            % (metrics["gross_edge_bps"], COST_WALL_MULTIPLE, cost_bps)
        )
        return _emit(
            VerdictStatus.COST_WALL,
            "The signal is under the cost floor: it needs to capture %s of the move "
            "it predicts."
            % (
                "more than the whole"
                if metrics["breakeven_capture"] is None
                else "%.0f%%" % (100.0 * metrics["breakeven_capture"])
            ),
            metrics=metrics,
            cost_rows=cost_rows,
            quality=quality,
            decisions=run.decisions,
            trades=run.trades,
            manifests=run.manifests,
        )
    passed.append(
        "gate 2 cost (gross %.2f bps against %.2f bps of cost)"
        % (metrics["gross_edge_bps"], cost_bps)
    )

    # ------------------------------------------------------ gate 3 robustness
    kill = spec.kill_criteria or {}
    promo = spec.promotion_criteria or {}
    rob = robustness_gate(
        metrics,
        min_profit_factor=float(promo.get("profit_factor_min", 1.20)),
        min_independent=int(promo.get("min_independent_events", 200)),
        max_drawdown_bps=kill.get("max_drawdown_bps"),
    )
    if rob:
        failed.extend("gate 3 robustness: %s" % r for r in rob)
        return _emit(
            VerdictStatus.REJECTED,
            "The result is not robust: %s" % "; ".join(rob),
            metrics=metrics,
            cost_rows=cost_rows,
            quality=quality,
            decisions=run.decisions,
            trades=run.trades,
            manifests=run.manifests,
        )
    passed.append("gate 3 robustness")

    # --------------------------------------------------------- gate 4 placebo
    declustered = decluster_frame(
        run.decisions,
        window_seconds=float(
            spec.declustering_rule.get("window_seconds")
            or spec.declustering_rule.get("same_symbol_gap_seconds")
        ),
        method=str(spec.declustering_rule["method"]),
        symbol_col=None if run.cross_sectional else "symbol",
    )
    suite = PlaceboSuite(declustered, panel=run.panel, n_draws=placebo_draws)
    placebo_results = suite.run(spec.placebo_tests)
    placebos = [p.to_dict() for p in placebo_results]
    pl_fail = placebo_gate(
        placebo_results,
        min_percentile=float(kill.get("placebo_percentile_lte", 95.0)),
    )
    if pl_fail:
        failed.extend("gate 4 placebo: %s" % p for p in pl_fail)
        return _emit(
            VerdictStatus.OVERFIT,
            "The mechanism does not beat the null its own design generates.",
            metrics=metrics,
            placebos=placebos,
            cost_rows=cost_rows,
            quality=quality,
            decisions=run.decisions,
            trades=run.trades,
            manifests=run.manifests,
        )
    passed.append("gate 4 placebo")

    # ---------------------------------------------------- gate 5 multiplicity
    if metrics["t_stat"] < threshold:
        failed.append(
            "gate 5 multiplicity: t on gross %.3f < %.3f, the threshold for %d "
            "hypotheses in family %s"
            % (
                metrics["t_stat"],
                threshold,
                metrics["multiplicity_family_size"],
                spec.multiplicity_family,
            )
        )
        return _emit(
            VerdictStatus.OVERFIT,
            "Below the bar this family has already paid for.",
            metrics=metrics,
            placebos=placebos,
            cost_rows=cost_rows,
            quality=quality,
            decisions=run.decisions,
            trades=run.trades,
            manifests=run.manifests,
        )
    passed.append(
        "gate 5 multiplicity (t %.3f >= %.3f for %d hypotheses)"
        % (metrics["t_stat"], threshold, metrics["multiplicity_family_size"])
    )

    # ------------------------------------------------- gate 6 forward seal
    seal = find_seal(spec.mechanism_id, root=sealed_root)
    if seal is None:
        passed.append("gate 6 pending: no forward seal yet")
        return _emit(
            VerdictStatus.PROMISING_NEEDS_FORWARD,
            "Every historical gate passed. This is a hypothesis worth sealing, not an "
            "edge: seal a forward window before any capital, paper included.",
            metrics=metrics,
            placebos=placebos,
            cost_rows=cost_rows,
            quality=quality,
            decisions=run.decisions,
            trades=run.trades,
            manifests=run.manifests,
        )

    verify_unchanged(spec, seal)
    passed.append("gate 6 forward seal %s" % seal.seal_id)
    return _emit(
        VerdictStatus.SEALED_FORWARD_ACTIVE
        if not seal.has_elapsed(ctx.now)
        else VerdictStatus.PROMISING_NEEDS_FORWARD,
        "Sealed forward window %s to %s. No promotion before it closes."
        % (seal.forward_start, seal.forward_end),
        metrics=metrics,
        placebos=placebos,
        cost_rows=cost_rows,
        quality=quality,
        decisions=run.decisions,
        trades=run.trades,
        manifests=run.manifests,
        seal_id=seal.seal_id,
    )


# ---------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run one mechanism and emit its verdict.")
    ap.add_argument("spec", help="path to mechanisms/<id>/spec.json")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--mode", default="historical", choices=["historical", "forward"])
    ap.add_argument("--placebo-draws", type=int, default=2000)
    ap.add_argument("--option", action="append", default=[], metavar="K=V")
    args = ap.parse_args(argv)

    options: Dict[str, Any] = {}
    for kv in args.option:
        k, _, v = kv.partition("=")
        options[k] = v

    ctx = RunContext(data_root=Path(args.data_root), mode=args.mode, options=options)
    verdict = run_mechanism(Path(args.spec), ctx=ctx, placebo_draws=args.placebo_draws)
    print(verdict.to_json())
    return 0 if verdict.status is not VerdictStatus.DATA_BROKEN else 2


if __name__ == "__main__":
    raise SystemExit(main())
