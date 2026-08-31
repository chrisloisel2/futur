"""
src/institutional/engines/vol_forecast_layer/combine.py
─────────────────────────────────────────────────────────────────────────────
Combine les 3 signaux bruts de VOL_FORECAST_LAYER_V1 (M2 rv_iv_spread, M6
far_otm_put_share, M17 block_count_24h) en UN SEUL forecast par jour.

MÉTHODE DE COMBINAISON (figée, documentée -- lire avant de modifier) :

  1. Chaque signal brut est converti en z-score CAUSAL glissant (fenêtre
     glissante Z_WINDOW_DAYS=180 jours calendaires, min_periods=
     Z_WINDOW_DAYS//2, utilisant UNIQUEMENT les données jusqu'à et y compris
     le jour t -- rolling().mean()/.std() calculés sur une fenêtre se
     TERMINANT à t, même convention causale déjà utilisée ailleurs dans ce
     projet, ex. src/institutional/features/volatility.py::vol_zscore,
     src/institutional/engines/whale_lsr_screen/screen.py
     ::compute_rolling_zscore). 180 jours est un DÉFAUT D'INGÉNIERIE (~6
     mois, assez long pour lisser le bruit de régime, assez court pour
     s'adapter) -- PAS fit/grid-searché sur ce dataset. Aucun lookahead :
     z(t) n'utilise jamais t+1..futur.

  2. Chaque z-score est ORIENTÉ pour que POSITIF signifie toujours "ce
     signal pointe vers une RV forward PLUS HAUTE", en utilisant le SIGNE
     rapporté dans le rapport source (w6_options/REPORT.md) :
       M2  (spread -> rv_fwd1d IC = -0.162, brut)            : oriented = -z(spread)
       M6  (otm_put_share -> rv_fwd1d IC partiel = +0.158)   : oriented = +z(otm_put_share)
       M17 (block_count -> rv_fwd24h IC partiel = +0.0996)   : oriented = +z(block_count)

  3. combined_forecast_z = moyenne À POIDS ÉGAUX des z-scores orientés
     DISPONIBLES ce jour-là (un signal peut être null les premiers jours
     avant que sa fenêtre 180j ne se remplisse, ou en cas de trou de
     donnée). Poids ÉGAUX, PAS une moyenne pondérée par IC : une pondération
     conjointe rigoureuse n'est pas disponible ici -- le chiffre le plus
     fort de M2 (IC partiel confound-checked -0.388) cible le FORWARD CHANGE
     DU SPREAD LUI-MÊME, pas la RV forward directement (une cible différente
     de celle sur laquelle M6/M17 sont évalués), donc l'utiliser comme poids
     inter-signaux mélangerait silencieusement deux cibles différentes.
     Plutôt que de choisir une pondération qu'on ne peut pas pleinement
     justifier, ce module utilise -- par instruction explicite -- l'approche
     la plus simple et défendable : moyenne à poids égaux. Voir
     freeze_spec.json pour le raisonnement complet.

  4. confidence (0..1, une HEURISTIQUE, jamais une probabilité) récompense
     l'accord entre les signaux orientés disponibles ET leur |IC| de
     référence moyen (REFERENCE_ABS_IC ci-dessous, constantes figées copiées
     verbatim du rapport source, utilisées SEULEMENT ici pour la confiance
     -- jamais pour la direction) :
       agreement = fraction des z-scores orientés disponibles dont le signe
                   correspond à sign(combined_forecast_z) (0 si combined==0)
       avg_ref_ic = moyenne de REFERENCE_ABS_IC sur les signaux disponibles
                    ce jour-là
       confidence = agreement * min(avg_ref_ic / IC_CONFIDENCE_ANCHOR, 1.0)
     IC_CONFIDENCE_ANCHOR=0.20 est un défaut d'ingénierie ("un IC autour de
     0.20 est à peu près le haut de ce que ce dataset produit après contrôle
     de confusion" -- le -0.39 partiel de M2 est l'outlier, sur une cible
     différente) -- pas fit.

  5. forecast_direction : "RV_UP" si combined_forecast_z > DIRECTION_Z_THRESHOLD,
     "RV_DOWN" si < -DIRECTION_Z_THRESHOLD, sinon "NEUTRAL".
     DIRECTION_Z_THRESHOLD=0.5 -- défaut d'ingénierie, pas fit.

Aucun ML nulle part dans ce module : chaque constante est une valeur fixe et
déclarée.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

Z_WINDOW_DAYS = 180
DIRECTION_Z_THRESHOLD = 0.5
IC_CONFIDENCE_ANCHOR = 0.20

# Constantes FIGÉES, copiées verbatim de reports/edge_discovery/
# alpha_hunt_2026-08-30/w6_options/REPORT.md et
# evidence/w6_mechanism_results.json -- JAMAIS refit ici.
REFERENCE_ABS_IC = {
    "rv_iv_spread": 0.1622,       # M2, spread_to_rv_fwd1d, BRUT (pas de partial calculé pour CETTE cible précise)
    "far_otm_put_share": 0.1583,  # M6, CONFOUND_partial_ic_controlling_for_sameday_rv
    "block_count_24h": 0.0996,    # M17, CONFOUND_partial_ic_controlling_for_trail_rv_24h
}
ORIENTATION_SIGN = {
    "rv_iv_spread": -1.0,       # spread HAUT -> historiquement RV forward PLUS BASSE
    "far_otm_put_share": +1.0,  # part HAUTE -> historiquement RV forward PLUS HAUTE
    "block_count_24h": +1.0,    # count HAUT -> historiquement RV forward PLUS HAUTE
}

SIGNAL_COLUMNS = ("rv_iv_spread", "far_otm_put_share", "block_count_24h")


def causal_zscore(series: pd.Series, window_days: int = Z_WINDOW_DAYS) -> pd.Series:
    """Z-score causal : moyenne/écart-type glissants sur `window_days` jours
    se terminant à t (INCLUT t -- chaque ligne de ce panel représente un
    jour calendaire déjà clos au moment où le forecast est émis).
    min_periods = window_days // 2 (tolère les trous)."""
    mu = series.rolling(window_days, min_periods=window_days // 2).mean()
    sigma = series.rolling(window_days, min_periods=window_days // 2).std()
    return (series - mu) / sigma.replace(0.0, np.nan)


def add_causal_zscores(panel: pd.DataFrame) -> pd.DataFrame:
    """Ajoute `<col>_z` et `<col>_oriented_z` pour chaque colonne de
    SIGNAL_COLUMNS présente dans `panel` (le panel doit être trié par jour,
    une ligne par jour)."""
    out = panel.sort_values("day").reset_index(drop=True).copy()
    for col in SIGNAL_COLUMNS:
        if col not in out.columns:
            continue
        z = causal_zscore(out[col])
        out[f"{col}_z"] = z
        out[f"{col}_oriented_z"] = z * ORIENTATION_SIGN[col]
    return out


def combine_forecast(panel_with_z: pd.DataFrame) -> pd.DataFrame:
    """Ajoute combined_forecast_z, n_signals_available, forecast_direction,
    confidence. `panel_with_z` doit déjà avoir les colonnes `<col>_oriented_z`
    (voir add_causal_zscores)."""
    out = panel_with_z.copy()
    oriented_cols = [f"{c}_oriented_z" for c in SIGNAL_COLUMNS if f"{c}_oriented_z" in out.columns]
    if not oriented_cols or out.empty:
        out["combined_forecast_z"] = pd.Series(dtype="float64", index=out.index)
        out["n_signals_available"] = pd.Series(dtype="int64", index=out.index)
        out["forecast_direction"] = pd.Series(dtype="object", index=out.index)
        out["confidence"] = pd.Series(dtype="float64", index=out.index)
        return out

    oriented = out[oriented_cols]
    out["n_signals_available"] = oriented.notna().sum(axis=1)
    out["combined_forecast_z"] = oriented.mean(axis=1, skipna=True)

    def _direction(z):
        if pd.isna(z):
            return None
        if z > DIRECTION_Z_THRESHOLD:
            return "RV_UP"
        if z < -DIRECTION_Z_THRESHOLD:
            return "RV_DOWN"
        return "NEUTRAL"

    out["forecast_direction"] = out["combined_forecast_z"].apply(_direction)

    ref_ic_map = {f"{c}_oriented_z": REFERENCE_ABS_IC[c] for c in SIGNAL_COLUMNS}

    def _confidence(row):
        available = [c for c in oriented_cols if pd.notna(row[c])]
        combined = row["combined_forecast_z"]
        if not available or pd.isna(combined) or combined == 0:
            return 0.0
        agree = sum(1 for c in available if np.sign(row[c]) == np.sign(combined))
        agreement = agree / len(available)
        avg_ref_ic = float(np.mean([ref_ic_map[c] for c in available]))
        return float(agreement * min(avg_ref_ic / IC_CONFIDENCE_ANCHOR, 1.0))

    out["confidence"] = out.apply(_confidence, axis=1)
    return out
