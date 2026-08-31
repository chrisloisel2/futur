"""
src/institutional/engines/whale_lsr_screen/screen.py
─────────────────────────────────────────────────────────────────────────────
WHALE_LSR_SCREEN_V1 — Live Alpha Lab, Mode A (SIGNAL SHADOW).

Source du mécanisme (verbatim en substance) :
reports/edge_discovery/alpha_hunt_2026-08-30/w10_forgotten_and_positioning/REPORT.md
"M3a" (rank 1, seul survivant sur 9 mécanismes positioning testés) :

    top-position ("whale") long/short ratio en extrême haut relatif à sa
    propre historique glissante 7 jours -> sous-performance RELATIVE
    (cross-sectionally demeaned) attendue à 24h. n=87 épisodes indépendants
    (déclusterés), gross -57.8bps, t=-2.82, p=0.006, signe stable sur les
    deux moitiés de l'échantillon (~45 jours, un seul régime haussier).

C'est un signal SHORT-SHAPED (bearish) -> SHORT_REJECTED (règle projet) le
rend NON déployable comme short direct. Le SEUL usage légitime documenté par
le rapport est un SCREEN : éviter/réduire les nouvelles entrées LONG
ailleurs dans le portefeuille quand le whale LSR d'un symbole est à un
extrême 7j. Ce module implémente EXACTEMENT ça : classify_screen() ne
produit jamais de champ "direction" ni de valeur "SHORT" -- seulement des
booléens de screen. Voir freeze_spec.json pour le detail des seuils et des
limites (PROVENANCE DU SEUIL NUMÉRIQUE ci-dessous, IMPORTANT).

Mirror (whale extrêmement SHORT -> sur-performance) : mécanisme symétrique,
directionnellement cohérent mais MARGINAL et NON CONFIRMÉ dans le rapport
source (p=0.09, n=39, needs_full_validation=true). Ce module l'implémente
dans une colonne SÉPARÉE (`mirror_flag_unconfirmed`), jamais mélangée avec
le screen principal, et jamais présentée avec la même confiance.

PROVENANCE DU SEUIL NUMÉRIQUE (lire avant de modifier) --------------------
Le REPORT.md source et ses 3 CSV d'evidence
(w10_forgotten_and_positioning/evidence/positioning_mechanisms_*.csv) ont
été lus intégralement (grep exhaustif sur "z-score", "percentile",
"threshold", "sigma", "decile", "quantile" sur tout alpha_hunt_2026-08-30 —
aucune constante numérique de classification "extreme" n'y figure). Le
script generateur original n'a pas été sauvegardé (le rapport indique
explicitement que ce worker n'a pas pu utiliser l'outil Write pendant son
run et que le report a été retranscrit a posteriori "verbatim en
substance"). La constante numérique exacte utilisée pour produire n=2034
raw / 87 déclusterés est donc IRRÉCUPÉRABLE des artefacts sauvegardés.

Reverse-engineering tenté (grid search z-score et rang-percentile causal
7j sur data/positioning réel, fenêtre 2026-07-16..2026-08-30 identique au
rapport) : aucun seuil symétrique unique ne reproduit exactement à la fois
le côté HIGH (n=2034-2041 raw) ET le côté LOW (n=896-958 raw) -- cohérent
avec une distribution du ratio intrinsèquement asymétrique, mais les deux
approches (z-score poolé, rang dans la fenêtre propre) convergent vers un
seuil correspondant approximativement au 99.6e-99.7e percentile poolé de
la déviation standardisée (~z équivalent 4.0-4.3) pour reproduire le
nombre brut de barres HIGH rapporté. C'est un seuil BEAUCOUP plus strict
qu'un simple "2-sigma" -- cohérent avec le fait que 87 épisodes
indépendants sur 47 symboles x 45 jours est un événement rare (~1
occurrence tous les ~24 jours par symbole), pas un événement fréquent.

Décision opérationnelle : Z_EXTREME_LONG_THRESHOLD = 4.0 (et son miroir
-4.0), le nombre rond le plus proche de la fourchette reverse-engineered
(3.9-4.3). Ce n'est PAS le chiffre verbatim du rapport (qui n'existe pas
dans les artefacts sauvegardés) -- c'est une RECONSTRUCTION documentée,
choisie pour rester dans le même ordre de rareté statistique que ce qui a
été effectivement mesuré. Les stats attendues figées dans freeze_spec.json
(net_bps=-57.8, n=87, p=0.006) sont donc un contexte QUALITATIF/DIRECTIONNEL
sur le mécanisme, PAS une cible de reproduction exacte au bar-level pour ce
seuil opérationnel -- à valider en live avant toute promotion.
----------------------------------------------------------------------------

Causalité : compute_rolling_zscore() calcule mean/std glissants sur les 7
jours PASSÉS en excluant strictement la barre courante (shift(1) appliqué
au résultat de la fenêtre glissante avant comparaison) -- aucun lookahead
possible, vérifié par tests/test_whale_lsr_screen.py.
"""
from __future__ import annotations

import pandas as pd

# Fenêtre glissante causale (jours calendaires, "7D" = time-based rolling).
ROLLING_WINDOW = "7D"

# Cadence attendue de l'archiveur : 5 minutes -> 288 barres/jour -> 2016
# barres pour 7 jours pleins. min_periods tolère ~10% de trous (gaps réseau,
# redémarrages de l'archiveur) sans invalider tout le calcul du z-score.
BARS_PER_DAY_EXPECTED = 288
MIN_PERIODS_BARS = int(0.9 * 7 * BARS_PER_DAY_EXPECTED)  # 1814

# Seuils figés -- voir "PROVENANCE DU SEUIL NUMÉRIQUE" ci-dessus. NE JAMAIS
# modifier ces valeurs après le freeze (reports/live_alpha_lab/
# WHALE_LSR_SCREEN_V1/freeze_spec.json) sans créer un nouvel alpha_id (_V2).
Z_EXTREME_LONG_THRESHOLD = 4.0     # main signal : whale LSR extrême haut -> screen (avoid_new_longs)
Z_EXTREME_SHORT_THRESHOLD = -4.0   # mirror, MARGINAL/NON CONFIRMÉ (p=0.09 dans le rapport source)

REQUIRED_COLUMNS = ("timestamp", "symbol", "longShortRatio")


def compute_rolling_zscore(
    df: pd.DataFrame,
    ratio_col: str = "longShortRatio",
    timestamp_col: str = "timestamp",
    symbol_col: str = "symbol",
    window: str = ROLLING_WINDOW,
    min_periods: int = MIN_PERIODS_BARS,
) -> pd.DataFrame:
    """Ajoute `z_score_7d` : déviation causale de `ratio_col` vs sa propre
    moyenne/écart-type glissants sur `window` (7 jours), PAR SYMBOLE.

    Causalité stricte : pour la barre à l'instant t, roll_mean/roll_std sont
    calculés sur la fenêtre glissante se terminant à t (fenêtre INCLUANT t),
    puis le résultat est décalé d'une position (`shift(1)`) avant d'être
    comparé à la valeur courante. Le z-score à l'instant t utilise donc
    exclusivement des observations à des instants < t (strictement
    antérieures) -- jamais la barre courante elle-même. Aucun lookahead.

    `df` doit contenir au moins REQUIRED_COLUMNS. Vide -> retourne un vide
    avec les colonnes attendues (pas de crash).
    """
    if df.empty:
        out = df.copy()
        out["z_score_7d"] = pd.Series(dtype="float64")
        return out

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"compute_rolling_zscore: colonnes manquantes: {missing}")

    parts = []
    for sym, g in df.sort_values(timestamp_col).groupby(symbol_col, sort=False):
        g = g.set_index(timestamp_col)
        raw_mean = g[ratio_col].rolling(window, min_periods=min_periods).mean()
        raw_std = g[ratio_col].rolling(window, min_periods=min_periods).std()
        # shift(1) => à l'instant t, on utilise la fenêtre glissante calculée
        # à t-1 (dernière barre STRICTEMENT antérieure), jamais celle qui
        # inclut la barre courante t.
        prior_mean = raw_mean.shift(1)
        prior_std = raw_std.shift(1)
        z = (g[ratio_col] - prior_mean) / prior_std.replace(0.0, pd.NA)
        g = g.reset_index()
        g["z_score_7d"] = z.to_numpy()
        parts.append(g)

    out = pd.concat(parts, ignore_index=True)
    return out.sort_values([symbol_col, timestamp_col]).reset_index(drop=True)


def classify_screen(
    df_with_z: pd.DataFrame,
    long_threshold: float = Z_EXTREME_LONG_THRESHOLD,
    short_threshold: float = Z_EXTREME_SHORT_THRESHOLD,
) -> pd.DataFrame:
    """Classifie `z_score_7d` en flags de SCREEN -- jamais en direction de
    trade. Ajoute deux colonnes booléennes, mutuellement exclusives :

    - `screen_flag` (bool) : signal PRINCIPAL, validé (n=87, p=0.006 dans le
      rapport source, en tant que mécanisme -- voir docstring module pour la
      nuance sur le seuil opérationnel reconstruit). True == "avoid new
      LONG entries elsewhere in the portfolio for this symbol right now".
      N'implique JAMAIS un ordre/short sur ce symbole lui-même.

    - `mirror_flag_unconfirmed` (bool) : mirror MARGINAL, NEEDS_FULL_VALIDATION
      (p=0.09, n=39 dans le rapport source). True == whale extrêmement short
      -> sur-performance relative POSSIBLE mais pas confirmée. Ne doit
      JAMAIS être traité avec la même confiance que `screen_flag`.

    Ce module n'émet et n'émettra JAMAIS de colonne `direction` ni de valeur
    littérale "SHORT" -- SHORT est institutionnellement rejeté
    (SHORT_REJECTED) et ce mécanisme est short-shaped par construction.
    """
    if df_with_z.empty:
        out = df_with_z.copy()
        out["screen_flag"] = pd.Series(dtype="bool")
        out["mirror_flag_unconfirmed"] = pd.Series(dtype="bool")
        return out

    if "z_score_7d" not in df_with_z.columns:
        raise ValueError("classify_screen: colonne z_score_7d manquante -- appeler compute_rolling_zscore() d'abord.")

    out = df_with_z.copy()
    z = out["z_score_7d"]
    out["screen_flag"] = (z >= long_threshold).fillna(False)
    out["mirror_flag_unconfirmed"] = (z <= short_threshold).fillna(False)
    # Les deux flags sont mutuellement exclusifs par construction
    # (long_threshold > 0 > short_threshold) -- pas d'assertion runtime ici
    # pour rester tolérant à des seuils custom en test, mais documenté.
    return out
