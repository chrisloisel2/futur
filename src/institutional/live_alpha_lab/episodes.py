"""
src/institutional/live_alpha_lab/episodes.py
─────────────────────────────────────────────────────────────────────────────
raw_signals != independent evidence (instruction utilisateur, item 11).
Deux décisions du même alpha sur le même symbole rapprochées dans le temps
ne sont pas 2 preuves indépendantes -- c'est le même piège de decluster déjà
documenté 4× dans le sweep de recherche (alpha_hunt round 2) et maintenant
appliqué au FORWARD tracking, pas seulement au backtest.

Règle simple, documentée, réutilisée partout dans ce projet (calendar basis,
liq_cascade) : deux décisions du MÊME symbole sont dans le MÊME cluster/
épisode si elles tombent à moins de `cluster_window_hours` l'une de l'autre.
Un épisode = 1 preuve indépendante, peu importe combien de décisions brutes
il contient."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass(frozen=True)
class EpisodeSummary:
    raw_signals: int
    same_symbol_clusters: int    # nb de clusters (symbol, cluster_id) distincts
    independent_episodes: int    # == same_symbol_clusters (alias explicite, voir docstring)


def decluster(df: pd.DataFrame, time_col: str, symbol_col: str = "symbol",
             cluster_window_hours: float = 24.0) -> pd.DataFrame:
    """Ajoute `cluster_id` (entier, par symbole) -- deux lignes du même
    symbole partagent un cluster_id si l'écart entre elles est <
    cluster_window_hours. Retourne le df avec cette colonne ajoutée, PAS
    dédupliqué (l'appelant choisit ensuite quelle ligne représente
    l'épisode, typiquement la première)."""
    if df.empty:
        out = df.copy()
        out["cluster_id"] = pd.Series(dtype="int64")
        return out
    out = df.copy()
    out["cluster_id"] = -1
    window = pd.Timedelta(hours=cluster_window_hours)
    next_id = 0
    for symbol, grp in out.groupby(symbol_col, sort=False):
        g = grp.sort_values(time_col)
        last_t = None
        for idx in g.index:
            t = pd.Timestamp(out.at[idx, time_col])
            if last_t is None or (t - last_t) > window:
                next_id += 1
            out.at[idx, "cluster_id"] = next_id
            last_t = t
    return out


def summarize(df: pd.DataFrame, time_col: str, symbol_col: str = "symbol",
             cluster_window_hours: float = 24.0) -> EpisodeSummary:
    if df.empty:
        return EpisodeSummary(0, 0, 0)
    clustered = decluster(df, time_col, symbol_col, cluster_window_hours)
    n_clusters = clustered["cluster_id"].nunique()
    return EpisodeSummary(raw_signals=len(df), same_symbol_clusters=n_clusters,
                          independent_episodes=n_clusters)
