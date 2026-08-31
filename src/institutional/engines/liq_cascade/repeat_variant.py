"""
src/institutional/engines/liq_cascade/repeat_variant.py
─────────────────────────────────────────────────────────────────────────────
LIQ_CASCADE_REPEAT_V1 — NEW_VARIANT dérivée de A7-TAIL-E1 (Live Alpha Lab,
reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md
rank 1, corroboré indépendamment par w9_cross_dataset_interactions/REPORT.md).

Ne modifie JAMAIS dataset.py/detector.py (le pipeline figé, déjà en shadow
live depuis plus longtemps pour le modèle LIQ_CASCADE lui-même) : ce module
consomme tel quel `n_events_sym_24h`, une colonne CAUSALE qui existe DÉJÀ
dans la sortie de `build_event_dataset()` (dataset.py — compte, par symbole,
les events strictement antérieurs dans les 24h précédentes ; vérifié ligne à
ligne identique à la définition utilisée par W2/W9). Ce module n'ajoute
qu'une classification onset/mid/exhaustion + un filtre de trade sur cette
colonne existante — aucune réimplémentation du comptage.

Constat mesuré (W2) : la 1ere cascade LONG_CASCADE sur un symbole (24h) a un
edge net négatif ; la 3e ou plus ("exhaustion", n_prior>=2) porte tout l'edge
positif. SHORT_SQUEEZE est explicitement BLOQUÉ ici (convention de signe non
résolue, voir SCOREBOARD.md "Also unresolved") — ce module ne classe/trade
QUE kind == "LONG_CASCADE".
"""
from __future__ import annotations

import pandas as pd

EXHAUSTION_MIN_PRIOR = 2   # n_prior>=2 => 3e occurrence ou plus


def classify_repeat_bucket(n_prior: int) -> str:
    if n_prior == 0:
        return "onset"
    if n_prior >= EXHAUSTION_MIN_PRIOR:
        return "exhaustion"
    return "mid"   # n_prior==1 (2e occurrence) — non testé/non tradé dans ce V1


def select_tradeable(events: pd.DataFrame) -> pd.DataFrame:
    """Filtre les events réellement tradeables par LIQ_CASCADE_REPEAT_V1.

    `events` = sortie de `build_event_dataset()` (doit contenir les colonnes
    `kind` et `n_events_sym_24h`, déjà causales par construction). Sélection :
    kind == LONG_CASCADE (SHORT_SQUEEZE explicitement bloqué) ET bucket ==
    exhaustion (n_events_sym_24h>=2). Ajoute `repeat_bucket` et une colonne
    `direction` fixe LONG."""
    if events.empty:
        out = events.copy()
        out["repeat_bucket"] = pd.Series(dtype="object")
        out["direction"] = pd.Series(dtype="object")
        return out
    df = events.copy()
    df["repeat_bucket"] = df["n_events_sym_24h"].apply(classify_repeat_bucket)
    tradeable = df[(df["kind"] == "LONG_CASCADE") & (df["repeat_bucket"] == "exhaustion")].copy()
    tradeable["direction"] = "LONG"
    return tradeable
