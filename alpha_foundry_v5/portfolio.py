from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Sleeve:
    sleeve_id: str
    economic_source_id: str
    pnl_column: str
    standalone_score: float
    marginal_portfolio_positive: bool = False


@dataclass(frozen=True)
class PortfolioAdmission:
    accepted_ids: Tuple[str, ...]
    rejected: Mapping[str, str]
    unique_economic_sources: int
    effective_independent_bets: float
    max_pairwise_abs_corr: float
    portfolio_ready: bool


def spearman_corr_matrix(pnl: pd.DataFrame) -> pd.DataFrame:
    ranked = pnl.apply(pd.to_numeric, errors="coerce").rank(method="average")
    return ranked.corr(method="pearson")


def effective_number_of_bets(corr: np.ndarray) -> float:
    matrix = np.asarray(corr, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.shape[0] == 0:
        return 0.0
    vals = np.linalg.eigvalsh(np.nan_to_num(matrix, nan=0.0))
    vals = np.clip(vals, 0.0, None)
    denom = float(np.sum(vals * vals))
    return float((np.sum(vals) ** 2) / denom) if denom > 1e-18 else 0.0


def admit_sleeves(sleeves: Sequence[Sleeve], pnl: pd.DataFrame, max_abs_corr: float = 0.25, min_unique_sources: int = 10, min_effective_bets: float = 6.0) -> PortfolioAdmission:
    accepted = []
    rejected: Dict[str, str] = {}
    for sleeve in sorted(sleeves, key=lambda s: float(s.standalone_score), reverse=True):
        if sleeve.pnl_column not in pnl:
            rejected[sleeve.sleeve_id] = "missing_pnl"
            continue
        same_source = next((x for x in accepted if x.economic_source_id == sleeve.economic_source_id), None)
        if same_source is not None:
            rejected[sleeve.sleeve_id] = "same_economic_source:%s" % same_source.sleeve_id
            continue
        conflict = None
        for other in accepted:
            pair = pnl[[sleeve.pnl_column, other.pnl_column]].apply(pd.to_numeric, errors="coerce").dropna()
            if len(pair) < 3:
                continue
            corr = float(pair.rank(method="average").corr().iloc[0, 1])
            if np.isfinite(corr) and abs(corr) > float(max_abs_corr) and not sleeve.marginal_portfolio_positive:
                conflict = (other.sleeve_id, abs(corr))
                break
        if conflict is not None:
            rejected[sleeve.sleeve_id] = "correlation_gate:%s:%.4f" % conflict
            continue
        accepted.append(sleeve)
    if accepted:
        matrix = spearman_corr_matrix(pnl[[s.pnl_column for s in accepted]])
        arr = matrix.to_numpy(dtype=float)
        mask = ~np.eye(len(arr), dtype=bool)
        max_corr = float(np.nanmax(np.abs(arr[mask]))) if len(arr) > 1 else 0.0
        enb = effective_number_of_bets(arr)
    else:
        max_corr = 0.0
        enb = 0.0
    sources = len({s.economic_source_id for s in accepted})
    ready = sources >= int(min_unique_sources) and enb >= float(min_effective_bets)
    return PortfolioAdmission(tuple(s.sleeve_id for s in accepted), rejected, sources, float(enb), max_corr, bool(ready))
