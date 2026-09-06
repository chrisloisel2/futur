from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .contracts import EconomicEvidence, ResearchStage, StatisticalEvidence


@dataclass(frozen=True)
class GatePolicy:
    discovery_min_abs_ic: float = 0.015
    discovery_max_q: float = 0.05
    discovery_max_block_p: float = 0.05
    discovery_min_ess: float = 200.0
    confirmation_min_ic: float = 0.05
    confirmation_min_dsr: float = 0.95
    confirmation_max_pbo: float = 0.10
    execution_min_pf: float = 1.30
    execution_min_capacity_usd: float = 200000.0
    max_pairwise_corr: float = 0.25


@dataclass(frozen=True)
class GateDecision:
    stage: ResearchStage
    passed: bool
    failures: tuple[str, ...]


DEFAULT_POLICY = GatePolicy()


class ValidationEngine:
    def __init__(self, policy: GatePolicy = DEFAULT_POLICY):
        self.policy = policy

    def statistical_gate(self, stage: ResearchStage, evidence: StatisticalEvidence, expected_sign: int = 1) -> GateDecision:
        if int(expected_sign) not in {-1, 1}:
            raise ValueError("expected_sign must be -1 or +1")
        failures = []
        if stage == ResearchStage.DEV_DISCOVERY:
            if not np.isfinite(evidence.ic) or abs(float(evidence.ic)) < self.policy.discovery_min_abs_ic:
                failures.append("ic")
            if not np.isfinite(evidence.q_value) or float(evidence.q_value) > self.policy.discovery_max_q:
                failures.append("fdr")
            if not np.isfinite(evidence.block_p) or float(evidence.block_p) > self.policy.discovery_max_block_p:
                failures.append("block_shuffle")
            if not np.isfinite(evidence.ess) or float(evidence.ess) < self.policy.discovery_min_ess:
                failures.append("ess")
            if not evidence.same_sign_halves:
                failures.append("same_sign_halves")
        elif stage == ResearchStage.INDEPENDENT_CONFIRMATION:
            if not evidence.independent_window:
                failures.append("independent_window")
            if not np.isfinite(evidence.ic) or int(expected_sign) * float(evidence.ic) < self.policy.confirmation_min_ic:
                failures.append("locked_sign_ic")
            if not np.isfinite(evidence.dsr_probability) or float(evidence.dsr_probability) < self.policy.confirmation_min_dsr:
                failures.append("dsr")
            if not np.isfinite(evidence.pbo) or float(evidence.pbo) > self.policy.confirmation_max_pbo:
                failures.append("pbo")
            if not evidence.all_primary_symbols_pass:
                failures.append("primary_symbols")
            if not evidence.same_sign_halves:
                failures.append("same_sign_halves")
        else:
            raise ValueError("statistical_gate only supports discovery/confirmation")
        return GateDecision(stage, not failures, tuple(failures))

    def economic_gate(self, evidence: EconomicEvidence, require_paper: bool = False) -> GateDecision:
        failures = []
        if not np.isfinite(evidence.net_edge_bps) or float(evidence.net_edge_bps) <= 0:
            failures.append("net_edge")
        if not np.isfinite(evidence.delayed_entry_net_bps) or float(evidence.delayed_entry_net_bps) <= 0:
            failures.append("delayed_entry")
        if not np.isfinite(evidence.top_contributors_removed_net_bps) or float(evidence.top_contributors_removed_net_bps) <= 0:
            failures.append("contributors")
        if not np.isfinite(evidence.recent_period_net_bps) or float(evidence.recent_period_net_bps) <= 0:
            failures.append("recent_period")
        # profit_factor may legitimately be +inf (zero losing trades); only NaN is a failure mode.
        if np.isnan(evidence.profit_factor) or float(evidence.profit_factor) < self.policy.execution_min_pf:
            failures.append("profit_factor")
        if not np.isfinite(evidence.capacity_usd) or float(evidence.capacity_usd) < self.policy.execution_min_capacity_usd:
            failures.append("capacity")
        if require_paper and (not np.isfinite(evidence.paper_live_net_bps) or float(evidence.paper_live_net_bps) <= 0):
            failures.append("paper_live")
        stage = ResearchStage.PAPER_LIVE if require_paper else ResearchStage.EXECUTION_ECONOMICS
        return GateDecision(stage, not failures, tuple(failures))


def profit_factor(trade_pnl: Sequence[float]) -> float:
    p = np.asarray(trade_pnl, dtype=float)
    gains = float(np.sum(p[p > 0]))
    losses = float(-np.sum(p[p < 0]))
    if losses <= 1e-18:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def max_drawdown(pnl_returns: Sequence[float]) -> float:
    r = np.asarray(pnl_returns, dtype=float)
    r = np.nan_to_num(r, nan=0.0)
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    dd = equity / np.where(peak > 0, peak, 1.0) - 1.0
    return float(np.min(dd)) if len(dd) else 0.0
