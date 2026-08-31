"""
src/institutional/engines/liq_cascade/far_from_low_variant.py
─────────────────────────────────────────────────────────────────────────────
LIQ_CASCADE_FAR_FROM_LOW_V1 — NEW_VARIANT dérivée de W2 rank 4 (Live Alpha Lab,
reports/edge_discovery/alpha_hunt_2026-08-30/w2_liquidation_leverage/REPORT.md
rank 4, "'Far from local low' beats 'at the low'"), corroborée par le detail
JSON evidence/liquidation_L1_L7_full.json §L5_preceding_trend_depth et
evidence/liquidation_L1_L5_deepdive_by_quarter_year.json §LONG_CASCADE_far24hlow.

Ne modifie JAMAIS dataset.py/detector.py (le pipeline figé) : ce module
consomme tel quel `dist_low_24h`, une colonne CAUSALE qui existe DÉJÀ dans la
sortie de `build_event_dataset()` (dataset.py::_causal_frame — `px / low24 -
1.0` où `low24` est un rolling(288, min_periods=144).min() STRICTEMENT
rétrospectif). Ce module n'ajoute qu'une classification near/far + un filtre
de trade sur cette colonne existante — aucune réimplémentation de la distance
au plus bas.

Constat mesuré (W2 rank 4, evidence L5_preceding_trend_depth) : parmi les
events LONG_CASCADE, ceux "loin" du plus-bas 24h (dist_low_24h dans le
quartile supérieur) battent nettement ceux "au ras" du plus-bas (dist_low_24h
≈ 0, ~exactement au plus bas au moment du cascade) : far_24h_low_full n=6709
net14=+4.48bps vs near_24h_low_full n=7169 net14=+6.05bps mais far_24h_low
2025-26 (OOS récent) net14=+17.7bps alors que near_24h_low 2025-26 devient
NÉGATIF (net14=-21.49bps) — d'où "far from local low beats at the low"
(near-low flips negative OOS, far-low stable/improving). SHORT_SQUEEZE est
explicitement BLOQUÉ ici (convention de signe non résolue, voir SCOREBOARD.md
"Also unresolved", même raison que LIQ_CASCADE_REPEAT_V1) — ce module ne
classe/trade QUE kind == "LONG_CASCADE".

Seuil gelé : le rapport ne publie pas un seuil numérique unique pour le split
near/far (buckets décrits en prose, "near"/"far" du plus-bas 24h), seulement
les tailles d'échantillon résultantes (n≈6709/6713 "far", n≈7169 "near", sur
une population LONG_CASCADE ≈26.8k au moment du rapport — evidence
liquidation_bucket_overlap_jaccard.json confirme n_b1=6713 pour far_24h_low).
Reproduit ici : recalcul de la distribution empirique de `dist_low_24h` sur
kind==LONG_CASCADE, univers figé (configs/portfolio_v1_1_parallel_50.yaml),
build_event_dataset() inchangé, au moment du gel (2026-08-31) -> 75e centile
mesuré = 0.0489 (4.89%), arrondi au seuil rond FAR_FROM_LOW_MIN_DIST_24H =
0.05 (5%) ci-dessous, qui reproduit la taille et la performance du bucket
"far_24h_low" du rapport à ±2% de N près et au même ordre de grandeur de
net_bps (voir reports/live_alpha_lab/LIQ_CASCADE_FAR_FROM_LOW_V1/freeze_spec.json
pour le détail de la reproduction). C'est un quartile (~25% supérieur de la
distribution), pas une médiane : le 25e centile de dist_low_24h est
littéralement 0.0 (beaucoup de cascades ont lieu EXACTEMENT au plus bas
24h), ce qui explique pourquoi near+far ne couvre pas 100% de la population
(zone "mid" non tradée, comme n_events_sym_24h==1 dans LIQ_CASCADE_REPEAT_V1).

dist_low_7d (alternative lookback) écartée : le fichier de deepdive du
rapport (liquidation_L1_L5_deepdive_by_quarter_year.json) ne détaille QUE la
variante 24h par trimestre/année (LONG_CASCADE_far24hlow_by_quarter/_by_year),
confirmant que c'est la variante 24h qui a été retenue/vérifiée pour la
stabilité temporelle citée dans le rapport ("stable, both kinds agree,
near-low flips negative OOS"). dist_low_7d donne des net_bps très proches
(evidence L5 : far_7d_low_full net14=5.74/OOS net14=15.48) mais n'a pas cette
même vérification par trimestre/année dans les preuves -> écartée par
prudence, pas par supériorité de performance.
"""
from __future__ import annotations

import pandas as pd

# Seuil gelé le 2026-08-31 -- NE PAS RECALCULER dynamiquement à chaque run
# (casserait la reproductibilité du freeze). Toute révision = nouvel alpha_id
# (_V2), pas une modification de cette constante. Voir docstring ci-dessus et
# freeze_spec.json pour la dérivation (~75e centile empirique de dist_low_24h
# sur LONG_CASCADE, univers figé, au moment du gel).
FAR_FROM_LOW_MIN_DIST_24H = 0.05   # 5% au-dessus du plus-bas 24h (rolling, causal)


def classify_low_bucket(dist_low_24h: float) -> str:
    """far si dist_low_24h >= seuil gelé (quartile supérieur), sinon near
    (regroupe le "at the low" du rapport et la zone mid non tradée -- seul
    "far" est tradeable dans ce V1)."""
    if pd.isna(dist_low_24h):
        return "near"   # feature manquante (warmup insuffisant) -> jamais tradeable
    if dist_low_24h >= FAR_FROM_LOW_MIN_DIST_24H:
        return "far"
    return "near"


def select_tradeable(events: pd.DataFrame) -> pd.DataFrame:
    """Filtre les events réellement tradeables par LIQ_CASCADE_FAR_FROM_LOW_V1.

    `events` = sortie de `build_event_dataset()` (doit contenir les colonnes
    `kind` et `dist_low_24h`, déjà causales par construction). Sélection :
    kind == LONG_CASCADE (SHORT_SQUEEZE explicitement bloqué) ET low_bucket ==
    far (dist_low_24h >= FAR_FROM_LOW_MIN_DIST_24H). Ajoute `low_bucket` et une
    colonne `direction` fixe LONG."""
    if events.empty:
        out = events.copy()
        out["low_bucket"] = pd.Series(dtype="object")
        out["direction"] = pd.Series(dtype="object")
        return out
    df = events.copy()
    df["low_bucket"] = df["dist_low_24h"].apply(classify_low_bucket)
    tradeable = df[(df["kind"] == "LONG_CASCADE") & (df["low_bucket"] == "far")].copy()
    tradeable["direction"] = "LONG"
    return tradeable
