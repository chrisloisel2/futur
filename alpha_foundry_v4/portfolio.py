from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd

from .contracts import AlphaCandidate


@dataclass(frozen=True)
class OrthogonalityResult:
    accepted: bool
    reason: str
    max_abs_corr: float
    conflicting_candidate: str


def _spearman_pair(a: pd.Series, b: pd.Series) -> float:
    pair = pd.DataFrame({"a": pd.to_numeric(a, errors="coerce"), "b": pd.to_numeric(b, errors="coerce")}).dropna()
    if len(pair) < 3:
        return float("nan")
    return float(pair["a"].rank().corr(pair["b"].rank()))


def pairwise_correlation_matrix(pnl: pd.DataFrame) -> pd.DataFrame:
    numeric = pnl.apply(pd.to_numeric, errors="coerce")
    return numeric.rank().corr()


def check_candidate_orthogonality(candidate: AlphaCandidate, accepted: Sequence[AlphaCandidate], pnl: pd.DataFrame, max_abs_corr: float) -> OrthogonalityResult:
    if candidate.pnl_series_name not in pnl:
        return OrthogonalityResult(False, "missing_candidate_pnl", float("nan"), "")
    for other in accepted:
        if other.independence_key == candidate.independence_key:
            return OrthogonalityResult(False, "same_independence_key", 1.0, other.candidate_id)
    strongest = 0.0
    strongest_id = ""
    for other in accepted:
        if other.pnl_series_name not in pnl:
            continue
        corr = _spearman_pair(pnl[candidate.pnl_series_name], pnl[other.pnl_series_name])
        if np.isfinite(corr) and abs(corr) > strongest:
            strongest = abs(corr)
            strongest_id = other.candidate_id
    if strongest > float(max_abs_corr) and not candidate.evidence.marginal_portfolio_positive:
        return OrthogonalityResult(False, "correlation_gate", strongest, strongest_id)
    return OrthogonalityResult(True, "orthogonal", strongest, strongest_id)


def count_independent_mechanisms(candidates: Iterable[AlphaCandidate]) -> int:
    return len({candidate.independence_key for candidate in candidates})


def greedy_independent_portfolio(candidates: Sequence[AlphaCandidate], pnl: pd.DataFrame, max_abs_corr: float = 0.25) -> Tuple[List[AlphaCandidate], Dict[str, OrthogonalityResult]]:
    accepted: List[AlphaCandidate] = []
    results: Dict[str, OrthogonalityResult] = {}
    ordered = sorted(candidates, key=lambda c: (c.evidence.net_edge_bps, c.evidence.dsr, c.evidence.sleeve_pf), reverse=True)
    for candidate in ordered:
        result = check_candidate_orthogonality(candidate, accepted, pnl, max_abs_corr=max_abs_corr)
        results[candidate.candidate_id] = result
        if result.accepted:
            accepted.append(candidate)
    return accepted, results
