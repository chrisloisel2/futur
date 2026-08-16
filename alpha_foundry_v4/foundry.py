from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

import pandas as pd

from .contracts import AlphaCandidate, PromotionEvidence
from .portfolio import count_independent_mechanisms, greedy_independent_portfolio
from .protocol import DEFAULT_GATES, ScientificGates
from .registry import LAB_REGISTRY


@dataclass(frozen=True)
class CandidateGateResult:
    passed: bool
    failures: Sequence[str]


@dataclass(frozen=True)
class FoundryVerdict:
    production_ready: bool
    accepted_candidate_ids: Sequence[str]
    independent_mechanisms: int
    target_independent_mechanisms: int
    rejection_reasons: Dict[str, Sequence[str]]


def evaluate_evidence(evidence: PromotionEvidence, gates: ScientificGates = DEFAULT_GATES) -> CandidateGateResult:
    failures: List[str] = []
    if gates.require_pit_clean and not evidence.pit_clean:
        failures.append("pit")
    if gates.require_independent_forward and not evidence.independent_forward:
        failures.append("independent_forward")
    if evidence.dsr < gates.min_dsr:
        failures.append("dsr")
    if evidence.pbo > gates.max_pbo:
        failures.append("pbo")
    if gates.require_cost_x2_positive and not evidence.cost_x2_positive:
        failures.append("cost_x2")
    if gates.require_delayed_entry_positive and not evidence.delayed_entry_positive:
        failures.append("delayed_entry")
    if gates.require_top_contributors_removed_positive and not evidence.top_contributors_removed_positive:
        failures.append("top_contributors_removed")
    if gates.require_same_sign_halves and not evidence.same_sign_halves:
        failures.append("same_sign_halves")
    if gates.require_recent_period_not_destructive and not evidence.recent_period_not_destructive:
        failures.append("recent_period")
    if evidence.sleeve_pf < gates.min_sleeve_pf:
        failures.append("sleeve_pf")
    if evidence.capacity_usd < gates.min_capacity_usd:
        failures.append("capacity")
    if gates.require_paper_live_positive and not evidence.paper_live_positive:
        failures.append("paper_live")
    if gates.require_mechanism_confirmed and not evidence.mechanism_confirmed:
        failures.append("mechanism_confirmation")
    if evidence.net_edge_bps <= 0:
        failures.append("net_edge")
    return CandidateGateResult(not failures, tuple(failures))


class AlphaFoundry:
    def __init__(self, gates: ScientificGates = DEFAULT_GATES):
        self.gates = gates

    def validate_candidate_contract(self, candidate: AlphaCandidate) -> CandidateGateResult:
        failures = []
        spec = LAB_REGISTRY.get(candidate.mechanism_id)
        if spec is None:
            failures.append("unknown_mechanism")
        elif spec.independence_key != candidate.independence_key:
            failures.append("independence_key_mismatch")
        evidence_result = evaluate_evidence(candidate.evidence, self.gates)
        failures.extend(evidence_result.failures)
        return CandidateGateResult(not failures, tuple(failures))

    def build_portfolio(self, candidates: Sequence[AlphaCandidate], pnl: pd.DataFrame) -> FoundryVerdict:
        eligible = []
        rejections: Dict[str, Sequence[str]] = {}
        for candidate in candidates:
            result = self.validate_candidate_contract(candidate)
            if result.passed:
                eligible.append(candidate)
            else:
                rejections[candidate.candidate_id] = result.failures
        accepted, ortho = greedy_independent_portfolio(eligible, pnl=pnl, max_abs_corr=self.gates.max_pairwise_corr)
        for candidate in eligible:
            result = ortho[candidate.candidate_id]
            if not result.accepted:
                rejections[candidate.candidate_id] = (result.reason,)
        independent = count_independent_mechanisms(accepted)
        return FoundryVerdict(production_ready=independent >= self.gates.min_independent_mechanisms, accepted_candidate_ids=tuple(candidate.candidate_id for candidate in accepted), independent_mechanisms=int(independent), target_independent_mechanisms=int(self.gates.min_independent_mechanisms), rejection_reasons=rejections)
