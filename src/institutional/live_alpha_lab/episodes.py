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


# ═══════════════════════════════════════════════════════════════════════════
# DECLUSTER v2 — versé À CÔTÉ de v1, jamais à sa place
# ═══════════════════════════════════════════════════════════════════════════
#
# Ce que v1 fait, et pourquoi c'est une pathologie connue
# ───────────────────────────────────────────────────────
# `decluster` ci-dessus regroupe par LIEN SIMPLE : un nouvel épisode s'ouvre
# quand l'écart avec le point PRÉCÉDENT dépasse la fenêtre. Une chaîne de
# points rapprochés fusionne donc en un bloc unique même si ses extrémités
# sont à six ans l'une de l'autre.
#
#     cadence de tir      épisodes sur 6 ans (fenêtre 24 h)
#     toutes les 23 h                                   1
#     exactement 24 h                                   1
#     toutes les 25 h                               2 102
#
# La falaise est entre 24 et 25 heures. Ce n'est pas un paramètre malheureux :
# c'est le comportement générique de toute stratégie qui tire régulièrement
# sur un même symbole. Le plafond de ~49 épisodes du placebo en est la même
# conséquence, et c'est ce qui a rendu trois alphas « indécidables ».
#
# Dans quel sens v1 se trompe
# ───────────────────────────
# v1 ne peut que SOUS-estimer l'indépendance : il fusionne des observations
# que v2 sépare, jamais l'inverse (`n_v2 >= n_v1` toujours). Les verdicts
# NÉGATIFS obtenus sous v1 restent donc valides — un intervalle trop large ne
# fabrique pas de faux négatif au sens où il exclurait à tort. Ce que v1
# détruit, c'est la PUISSANCE : l'appareil était plus myope qu'il n'avait
# besoin de l'être.
#
# Pourquoi v1 n'est pas supprimée
# ────────────────────────────────
# Tous les comptes déjà publiés — scoreboard, verdicts, item A1, contrôle
# positif — ont été calculés sous v1. Les réécrire silencieusement rendrait
# incomparables les chiffres d'avant et d'après, et ferait disparaître la
# trace de ce qui a été décidé sur quelle base. Les deux définitions coexistent
# donc, chacune sous son nom, et les rapports publient les deux colonnes.

DECLUSTER_V1 = "v1_single_linkage"
DECLUSTER_V2 = "v2_complete_linkage"
DECLUSTER_V2_FIXED = "v2_fixed_windows"

DECLUSTER_VERSIONS = {
    DECLUSTER_V1: "lien simple — un épisode s'étend tant que l'écart au point PRÉCÉDENT "
                  "reste sous la fenêtre. Chaîne indéfiniment.",
    DECLUSTER_V2: "lien complet — un épisode s'étend tant que l'écart à son PROPRE DÉBUT "
                  "reste sous la fenêtre. Durée bornée par la fenêtre, pas de chaînage.",
    DECLUSTER_V2_FIXED: "fenêtres fixes non recouvrantes ancrées sur l'époque — pas de "
                        "chaînage non plus, mais la phase des bornes est arbitraire.",
}


def decluster_v2(df: pd.DataFrame, time_col: str, symbol_col: str = "symbol",
                 cluster_window_hours: float = 24.0) -> pd.DataFrame:
    """LIEN COMPLET : un épisode ne peut jamais durer plus que la fenêtre.

    Un nouvel épisode s'ouvre dès que l'écart au DÉBUT de l'épisode courant
    dépasse la fenêtre — et non l'écart au point précédent. Des décisions
    espacées de 3 h donnent donc un épisode toutes les 24 h, ce qui est la
    lecture qu'on voulait depuis le début : « combien de fenêtres de 24 h
    distinctes ce symbole a-t-il produit ».

    Garantie : `n_clusters_v2 >= n_clusters_v1` sur toute entrée. Cette
    version ne peut qu'augmenter la taille d'échantillon.
    """
    if df.empty:
        out = df.copy()
        out["cluster_id"] = pd.Series(dtype="int64")
        return out
    out = df.copy()
    out["cluster_id"] = -1
    window = pd.Timedelta(hours=cluster_window_hours)
    next_id = 0
    for _symbol, grp in out.groupby(symbol_col, sort=False):
        g = grp.sort_values(time_col)
        anchor = None
        for idx in g.index:
            t = pd.Timestamp(out.at[idx, time_col])
            if anchor is None or (t - anchor) > window:
                next_id += 1
                anchor = t          # le point qui OUVRE l'épisode, jamais réactualisé
            out.at[idx, "cluster_id"] = next_id
    return out


def decluster_fixed_windows(df: pd.DataFrame, time_col: str, symbol_col: str = "symbol",
                            cluster_window_hours: float = 24.0) -> pd.DataFrame:
    """Fenêtres fixes ancrées sur l'époque UTC. Sans chaînage non plus.

    Avantage sur le lien complet : le découpage ne dépend pas de la première
    observation, donc deux alphas sur le même symbole partagent leurs bornes
    d'épisode et sont directement comparables. Inconvénient : la phase est
    arbitraire, et une décision juste avant une borne se retrouve seule dans
    son épisode.
    """
    if df.empty:
        out = df.copy()
        out["cluster_id"] = pd.Series(dtype="int64")
        return out
    out = df.copy()
    stamps = pd.to_datetime(out[time_col], utc=True)
    bucket = (stamps.astype("int64") // int(cluster_window_hours * 3_600_000_000_000))
    # L'identifiant doit rester unique PAR SYMBOLE : deux symboles dans la même
    # fenêtre calendaire sont deux épisodes, pas un.
    keys = out[symbol_col].astype(str) + "|" + bucket.astype(str)
    out["cluster_id"] = pd.factorize(keys)[0] + 1
    return out


_DECLUSTER_IMPL = {
    DECLUSTER_V1: decluster,
    DECLUSTER_V2: decluster_v2,
    DECLUSTER_V2_FIXED: decluster_fixed_windows,
}


def decluster_versioned(df: pd.DataFrame, time_col: str, symbol_col: str = "symbol",
                        cluster_window_hours: float = 24.0,
                        version: str = DECLUSTER_V1) -> pd.DataFrame:
    """Point d'entrée versionné. Le défaut reste v1 — changer le défaut
    changerait en silence tout compte déjà publié, ce qui est exactement ce
    qu'on refuse de faire."""
    if version not in _DECLUSTER_IMPL:
        raise ValueError("version de decluster inconnue : %r (connues : %s)"
                         % (version, sorted(_DECLUSTER_IMPL)))
    return _DECLUSTER_IMPL[version](df, time_col, symbol_col, cluster_window_hours)
