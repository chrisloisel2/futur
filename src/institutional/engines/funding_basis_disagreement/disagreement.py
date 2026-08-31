"""
src/institutional/engines/funding_basis_disagreement/disagreement.py
─────────────────────────────────────────────────────────────────────────────
FUNDING_BASIS_DISAGREEMENT_V1 -- classification RICH/CHEAP figée + décluster
en deux passes (identique en esprit à
w4_calendar_basis/analyze.py::episode_entries + nonoverlap_filter, lu comme
référence, jamais importé -- réimplémentation propre pour ce module de
production).

Mécanisme (M7, reports/edge_discovery/alpha_hunt_2026-08-30/w4_calendar_basis/
REPORT.md) : disagreement = funding_ann_pct - basis_near_ann.
  RICH (>=hi)  : funding >> basis trimestriel -> pari que la basis MONTE
                 pour rattraper le funding -> LONG quarterly / SHORT perp.
  CHEAP (<=lo) : funding << basis trimestriel (funding profondément négatif,
                 régime bear/de-risking, PAS juste une basis extrême relabelée
                 -- vérifié dans le rapport source par corrélation avec
                 basis_ann seule = 0.49-0.52 seulement) -> pari que la basis
                 BAISSE pour rattraper le funding -> SHORT quarterly / LONG perp.

Seuils FIGÉS au freeze (train-fit quantiles 10/90 du rapport source, section
M7) -- JAMAIS recalculés dynamiquement ici, ce serait du data-snooping sur la
fenêtre courte du flux funding live (voir panel.py docstring, ~2 mois
d'historique seulement au 2026-08-31). Toute évolution de seuil = nouvel
alpha_id (_V2), pas une édition de ce module (règle registry).

Horizon FIGÉ : k30d (voir freeze_spec.json pour la justification -- seul
horizon testé dans le rapport source où BTC ET ETH passent le coût de base
(14bps) ET le coût de stress (28bps) simultanément). Sert ici de fenêtre de
non-chevauchement (deuxième passe de décluster) -- ce module NE CALCULE AUCUN
PnL/exit (Mode A signal pur, pas de fill simulé).
"""
from __future__ import annotations

import pandas as pd

# Seuils figés (quantiles train-fit 10%/90% de `disagreement`, w4 REPORT.md
# section M7 -- "BTC (q10=-6.7, q90=+12.3 ann. disagreement)",
# "ETH (q10=-5.8, q90=+15.6 ann. disagreement)").
FROZEN_THRESHOLDS = {
    "BTCUSDT": {"lo": -6.7, "hi": 12.3},
    "ETHUSDT": {"lo": -5.8, "hi": 15.6},
}

FROZEN_HORIZON_DAYS = 30   # k30d -- voir freeze_spec.json pour la justification

DIRECTION_FOR_REGIME = {
    "RICH": "LONG_QUARTERLY_SHORT_PERP",
    "CHEAP": "SHORT_QUARTERLY_LONG_PERP",
}

_DECISION_COLUMNS = [
    "date", "symbol", "regime", "direction", "disagreement", "basis_near_ann",
    "funding_ann_pct", "near_dte", "near_contract", "threshold_lo", "threshold_hi",
]


def classify_regime(value: float, lo: float, hi: float) -> str:
    """RICH si value>=hi, CHEAP si value<=lo, sinon NEUTRAL. Bornes incluses
    (identique à w4/analyze.py::classify -- np.where(s>=hi,...,s<=lo,...))."""
    if value >= hi:
        return "RICH"
    if value <= lo:
        return "CHEAP"
    return "NEUTRAL"


def _mark_episode_starts(regime: pd.Series, dates: pd.Series) -> pd.Series:
    """True à l'indice 0, et à chaque indice où le régime change vs la ligne
    précédente OU où un trou calendaire (>1 jour) apparaît -- réplique
    build_episodes() de la recherche (une ligne = un jour déjà filtré
    near_dte>=MIN_DTE, donc un trou ici veut dire des jours inéligibles
    entre deux lignes, ce qui doit aussi couper l'épisode)."""
    n = len(regime)
    if n == 0:
        return pd.Series([], dtype=bool)
    regime_arr = regime.reset_index(drop=True)
    day_diff = dates.reset_index(drop=True).diff().dt.days.fillna(1)
    changed = pd.Series(False, index=range(n))
    changed.iloc[0] = True
    if n > 1:
        regime_changed = (regime_arr.values[1:] != regime_arr.values[:-1])
        gap = (day_diff.values[1:] > 1)
        changed.iloc[1:] = regime_changed | gap
    return changed


def _nonoverlap_filter(entries: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    """Décluster #2 (glouton, chronologique) : ne garde une entrée que si elle
    démarre au moins `horizon_days` après la DERNIÈRE entrée conservée --
    garantit qu'aucune paire de fenêtres retenues ne se chevauche dans le
    temps calendaire. Purement causal : la décision à la ligne i ne dépend
    que des lignes <= i (traitement gauche->droite)."""
    entries = entries.sort_values("date").reset_index(drop=True)
    keep_idx = []
    last_date = None
    for i, row in entries.iterrows():
        d = row["date"]
        if last_date is None or (d - last_date).days >= horizon_days:
            keep_idx.append(i)
            last_date = d
    return entries.loc[keep_idx].reset_index(drop=True)


def select_tradeable(panel: pd.DataFrame, thresholds: dict = None,
                     horizon_days: int = FROZEN_HORIZON_DAYS) -> pd.DataFrame:
    """Filtre le panel causal (sortie de panel.build_panel, une ou plusieurs
    symboles concaténés) aux entrées réellement tradeables par
    FUNDING_BASIS_DISAGREEMENT_V1 : régime RICH/CHEAP figé, début d'épisode
    (décluster #1, contiguïté de régime), ET non-chevauchement avec la
    dernière entrée conservée du même symbole sur `horizon_days` (décluster
    #2). Symboles absents de `thresholds` (par défaut FROZEN_THRESHOLDS) sont
    ignorés -- fail-closed, pas de seuil deviné pour un symbole non figé.

    Déterministe et recalculé depuis l'historique complet à chaque appel (pas
    d'état externe) -- le runner s'appuie là-dessus pour l'idempotence
    (dédup par (date, symbol) contre le ledger existant)."""
    thresholds = thresholds if thresholds is not None else FROZEN_THRESHOLDS
    if panel.empty:
        return pd.DataFrame(columns=_DECISION_COLUMNS)

    out_frames = []
    for symbol, grp in panel.groupby("symbol"):
        if symbol not in thresholds:
            continue
        lo, hi = thresholds[symbol]["lo"], thresholds[symbol]["hi"]
        grp = grp.sort_values("date").reset_index(drop=True)
        grp["regime"] = grp["disagreement"].apply(lambda v: classify_regime(v, lo, hi))
        grp["episode_start"] = _mark_episode_starts(grp["regime"], grp["date"])
        candidates = grp[grp["episode_start"] & grp["regime"].isin(["RICH", "CHEAP"])].copy()
        if candidates.empty:
            continue
        kept = _nonoverlap_filter(candidates, horizon_days)
        if kept.empty:
            continue
        kept["direction"] = kept["regime"].map(DIRECTION_FOR_REGIME)
        kept["threshold_lo"] = lo
        kept["threshold_hi"] = hi
        out_frames.append(kept[_DECISION_COLUMNS])

    if not out_frames:
        return pd.DataFrame(columns=_DECISION_COLUMNS)
    return pd.concat(out_frames, ignore_index=True)
