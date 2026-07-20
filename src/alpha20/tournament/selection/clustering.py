"""
src/alpha20/tournament/selection/clustering.py — regroupement par corrélation
et sélection du dominant (étape 7 : "conserve au maximum un dominant par
cluster"). Corrélation calculée sur les rendements quotidiens de NAV alignés
(intersection des dates communes) — pas de proxy famille seul (deux runners
de familles différentes peuvent être corrélés, deux runners d'une même
famille peuvent ne pas l'être).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from src.alpha20.tournament.paper_account import PaperAccount
from src.alpha20.tournament.selection.manifest import load_protocol


def _returns(spec) -> pd.Series:
    return PaperAccount(spec.runner_id, spec.capital_standalone_eur).daily_returns()


def correlation_matrix(specs) -> pd.DataFrame:
    series = {s.runner_id: _returns(s) for s in specs}
    df = pd.DataFrame(series)
    return df.corr(min_periods=10)


def cluster_by_correlation(corr: pd.DataFrame, threshold: float) -> List[List[str]]:
    """Composantes connexes du graphe |corr| ≥ seuil (union-find naïf)."""
    ids = list(corr.columns)
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in ids:
        for j in ids:
            if i >= j:
                continue
            v = corr.loc[i, j]
            if pd.notna(v) and abs(v) >= threshold:
                union(i, j)
    groups: Dict[str, List[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def select_dominants(specs, statuses: Dict[str, dict]
                     ) -> Tuple[List[str], List[Dict]]:
    proto = load_protocol()["phase_c_selection"]
    threshold = proto["cluster_correlation_threshold"]
    corr = correlation_matrix(specs)
    clusters = cluster_by_correlation(corr, threshold)

    def score(rid: str) -> float:
        st = statuses.get(rid, {})
        lcb = st.get("bootstrap_lcb95")
        if lcb is not None:
            return lcb
        return st.get("return_total") or float("-inf")

    dominants, report = [], []
    for members in clusters:
        best = max(members, key=score)
        dominants.append(best)
        report.append({"members": members, "dominant": best,
                       "scores": {m: score(m) for m in members}})
    return dominants, report
