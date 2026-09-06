#!/usr/bin/env python3
"""
scripts/run_carry_shadow_replay.py
─────────────────────────────────────────────────────────────────────────────
Phase 4D commits 8-9: the real, frozen-window carry shadow replay and its
differential classification.

Runs `carry_basis_v12` (unchanged config, unchanged signals/sizing/costs)
against the real BTCUSDT/ETHUSDT data over the window frozen in
docs/PHASE4D_FROZEN_WINDOW_DECISION.md (decided and committed BEFORE this
script was ever run), through CarryBasisShadowRunner, and classifies every
state-changing Truth event against the legacy result with
DifferentialComparator.

ONE real decide() call is sufficient: CarryBasisAdapter always re-runs the
whole backtest from paper_start to the true end, so a single call already
produces the COMPLETE, final leg_ledger (every leg's real entry_time and
exit_time) and the complete real per-asset price series for the whole
window. MARK events are sampled directly from each leg's own real
lifetime against that price series (mapping.py's
_mark_events_for_leg) -- NOT from "is this leg currently open," which an
earlier version of this script tried to approximate with repeated
truncated re-runs of MultiLegBacktester at different "as of" cutoffs.
That approach was abandoned: MultiLegBacktester.run() force-closes every
still-open position at its OWN `end` when a backtest window ends, so a
leg is NEVER observed "genuinely still open" through independently
truncated re-runs, at ANY cadence -- a structural dead end, not a
granularity problem. Sampling from each leg's own known real lifetime in
a single pass sidesteps it entirely, with no data truncation/restoration
risk to the large enriched files on disk.

Writes:
  - data/manifests/carry_shadow_replay_report.json  (provenance, coverage,
    classification counts -- no profitability figures)
  - data/manifests/carry_shadow_differential.jsonl  (the full append-only
    differential log, one line per (event, field) comparison)

Also runs the SAME real decide() call a second time with the shadow
DISABLED and confirms the legacy (events, new_state) content is identical
either way -- proof the shadow has no effect on the runner regardless of
whether it's on.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alpha20.tournament.market_bus import MarketSnapshot
from src.alpha20.tournament.runner_adapters import build_adapter
from src.alpha20.tournament.runner_registry import get_spec
from src.alpha20.tournament.truth_shadow.comparator import DifferentialComparator, DifferentialLog
from src.alpha20.tournament.truth_shadow.shadow_runner import (
    CarryBasisShadowRunner,
    ShadowConfig,
)
from src.futur.truth.events import EventType

MANIFEST_DIR = ROOT / "data" / "manifests"
RUNNER_ID = "carry_basis_v12"
VENUE = "binance_usdm"
PAPER_START = "2026-05-29"   # docs/PHASE4D_FROZEN_WINDOW_DECISION.md -- frozen before this ran

REQUIRED_COVERAGE = (
    "spot_leg_opened", "perp_leg_opened", "spot_mark", "perp_mark",
    "funding", "fee", "reduction_or_close", "terminal_close",
)


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _snapshot() -> MarketSnapshot:
    ts = "2026-07-28T21:00:00Z"
    return MarketSnapshot(market_event_id="phase4d-commit8", cutoff=ts,
                          decision_ts=ts, received_ts=ts)


def _events_equal(a_list, b_list) -> bool:
    if len(a_list) != len(b_list):
        return False
    return all(a.kind == b.kind and a.sleeve == b.sleeve and a.amount_usdt == b.amount_usdt
              for a, b in zip(a_list, b_list, strict=True))


def _classify_coverage(applied_events, engine) -> dict[str, list[str]]:
    coverage: dict[str, list[str]] = {k: [] for k in REQUIRED_COVERAGE}
    for ev in applied_events:
        payload = ev.payload
        if ev.event_type == EventType.FILL:
            instrument = payload.instrument
            if ev.event_id.endswith("-fill-entry") and instrument.type.value == "SPOT":
                coverage["spot_leg_opened"].append(ev.event_id)
            if ev.event_id.endswith("-fill-entry") and instrument.type.value == "LINEAR_PERP":
                coverage["perp_leg_opened"].append(ev.event_id)
            if ev.event_id.endswith("-fill-exit"):
                coverage["reduction_or_close"].append(ev.event_id)
        elif ev.event_type == EventType.MARK:
            instrument = payload.instrument
            (coverage["spot_mark"] if instrument.type.value == "SPOT"
             else coverage["perp_mark"]).append(ev.event_id)
        elif ev.event_type == EventType.FUNDING:
            coverage["funding"].append(ev.event_id)
        elif ev.event_type == EventType.FEE:
            coverage["fee"].append(ev.event_id)
    for key, pos in {**engine.account.spot_positions, **engine.account.perp_positions}.items():
        if pos.quantity == 0:
            coverage["terminal_close"].append(key)
    return coverage


def main() -> None:
    spec = get_spec(RUNNER_ID)
    shadow_execution_commit = _git_head()
    state = {"paper_start": PAPER_START}

    # ── shadow enabled: the real replay ──────────────────────────────────
    runner = CarryBasisShadowRunner(spec, config=ShadowConfig(enabled=True, venue=VENUE))
    events_on, new_state_on, shadow_result = runner.run_cycle(_snapshot(), dict(state))

    # ── shadow disabled: same real call, must be unaffected ─────────────
    events_off, new_state_off = build_adapter(spec).decide(_snapshot(), broker=None,
                                                            state=dict(state))

    legacy_identical = _events_equal(events_on, events_off) and new_state_on == new_state_off

    report: dict = {
        "schema_version": 2,
        "runner_id": RUNNER_ID,
        "venue": VENUE,
        "provenance": {
            "shadow_execution_commit": shadow_execution_commit,
            "historical_experiment_commit": spec.git_commit,
            "registry_config_hash": spec.config_hash,
            "effective_config_sha256": shadow_result.effective_config_sha256,
        },
        "window": {"paper_start": PAPER_START, "end": "2026-07-28T21:00:00Z",
                  "n_decide_calls": 1,
                  "frozen_decision_doc": "docs/PHASE4D_FROZEN_WINDOW_DECISION.md"},
        "legacy_identical_shadow_on_vs_off": legacy_identical,
        "n_legacy_events_on": len(events_on),
        "n_legacy_events_off": len(events_off),
    }

    if not shadow_result.ok:
        err = shadow_result.error
        err_type = type(err).__name__ if err is not None else None
        blocked = "BLOCKED_PRODUCT_SPEC" if err_type == "ProductSpecUnavailableError" else \
            "BLOCKED_MARK_SOURCE" if err_type == "MarkSourceUnavailableError" else None
        report["verdict"] = blocked or "SHADOW_ERROR"
        report["error"] = f"{err_type}: {err}"
        _write_report(report)
        print(json.dumps(report, indent=2, default=str))
        return

    if shadow_result.leg_ledger is None or shadow_result.leg_ledger.empty:
        report["verdict"] = "BLOCKED_COVERAGE"
        report["coverage_counts"] = {k: 0 for k in REQUIRED_COVERAGE}
        report["missing_coverage"] = list(REQUIRED_COVERAGE)
        _write_report(report)
        print(json.dumps(report, indent=2, default=str))
        return

    coverage = _classify_coverage(shadow_result.applied_events, runner.engine)
    missing = [k for k, v in coverage.items() if not v]
    report["coverage_counts"] = {k: len(v) for k, v in coverage.items()}
    report["missing_coverage"] = missing

    if missing:
        report["verdict"] = "BLOCKED_COVERAGE"
        _write_report(report)
        print(json.dumps(report, indent=2, default=str))
        return

    # ── differential classification (commit 9) ──────────────────────────
    run_id = f"phase4d-{shadow_execution_commit[:12]}"
    log_path = MANIFEST_DIR / "carry_shadow_differential.jsonl"
    if log_path.exists():
        log_path.unlink()   # fresh run -- this script's own output, not the frozen inputs
    log = DifferentialLog(log_path)
    comparator = DifferentialComparator(run_id=run_id, venue=VENUE)
    rows = comparator.compare_cycle(runner.engine, shadow_result.applied_events,
                                    shadow_result.leg_ledger, shadow_result.portfolio_ledger, log,
                                    account_snapshots=shadow_result.account_snapshots)
    log.close()

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.classification] = counts.get(r.classification, 0) + 1
    report["differential"] = {
        "log_file": str(log_path.relative_to(ROOT)), "n_rows": len(rows),
        "classification_counts": counts,
    }

    n_mapping_error = counts.get("SHADOW_MAPPING_ERROR", 0)
    n_unexplained = counts.get("UNEXPLAINED_DIVERGENCE", 0)
    if n_mapping_error > 0:
        report["verdict"] = "FAILED_UNEXPLAINED_DIVERGENCE"
        report["cause"] = f"{n_mapping_error} SHADOW_MAPPING_ERROR rows -- fix mapping/comparator " \
            f"in a SEPARATE commit, then re-run from this same frozen manifest"
    elif n_unexplained > 0:
        report["verdict"] = "FAILED_UNEXPLAINED_DIVERGENCE"
        report["cause"] = f"{n_unexplained} UNEXPLAINED_DIVERGENCE rows"
    elif not legacy_identical:
        report["verdict"] = "FAILED_UNEXPLAINED_DIVERGENCE"
        report["cause"] = "shadow on/off produced different legacy results"
    else:
        report["verdict"] = "TRUTH_ENGINE_CARRY_SHADOW_VALIDATED"

    _write_report(report)
    print(json.dumps(report, indent=2, default=str))


def _write_report(report: dict) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    out = MANIFEST_DIR / "carry_shadow_replay_report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")


if __name__ == "__main__":
    main()
