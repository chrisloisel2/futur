"""
level_0/constants.py — SOURCE DE VÉRITÉ UNIQUE
===============================================

Tout composant qui a besoin d'un horizon, d'un coût, d'un split ou d'un
paramètre global DOIT importer depuis ce fichier.

Ne jamais hardcoder ces valeurs ailleurs dans le projet.
Si une valeur doit changer, elle change ici et seulement ici.

─── Historique des horizons ──────────────────────────────────────────────────
  1h (initial)  : PF médian 0.87 — signal < random, coûts absorbent tout
  4h (v2)       : PF médian 1.10 — signal positif mais 3/7 folds seulement
  8h (v3)       : SNR cost/std 4.88% vs 6.76% (4h) → amélioration ~30%
                  Walk-forward résultat cible : ≥ 5/7 folds PF ≥ 1.20

Changements v3 vs v2 :
  • HORIZON_BARS  : 4 → 8  (prédiction 8h, position tenue 8h)
  • TARGET_COL    : future_ret_4h → future_ret_8h
  • LONG_MIN_ABS_RETURN : 0.8% → 1.5%  (frais/mouvement < 7%)
  • NON_REVERSAL_WINDOW_LONG : 6 → 16  (2× horizon = 16h anti-dip)
  • Reversal cols : future_ret_h8_min/max → future_ret_h16_min/max
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# HORIZON TEMPOREL — convention unique pour tout le pipeline
# ─────────────────────────────────────────────────────────────────────────────
# Données : barres 1h (fréquence inchangée — on conserve la granularité fine).
# Prédiction : rendement cumulé des HORIZON_BARS prochaines barres = 8h.
# SNR amélioré car coûts fixes < 7% du mouvement attendu (vs ~10% en 4h).

HORIZON_BARS: int = 8          # barres forward pour le label (8 × 1h = 8h)
HORIZON_MINUTES: int = 480     # durée réelle de la position en minutes
BAR_FREQUENCY: str = "1h"      # fréquence des données brutes (inchangée)
PREDICTION_HORIZON_STR: str = "8h"  # chaîne lisible pour les logs et rapports

# ─────────────────────────────────────────────────────────────────────────────
# COÛTS DE TRANSACTION
# ─────────────────────────────────────────────────────────────────────────────
COST_PCT: float = 0.0010        # 10 bps round-trip — conservateur de base
COST_PCT_STRESS: float = 0.0020 # 20 bps pour les tests de robustesse
COST_PCT_PESSIMISTIC: float = 0.0030  # 30 bps — cas extrême

# ─────────────────────────────────────────────────────────────────────────────
# LABELING
# ─────────────────────────────────────────────────────────────────────────────
TRADEABLE_QUANTILE: float = 0.70       # legacy — ne plus utiliser directement

# LONG — calibré pour l'horizon 8h
# Quantile 80 = top 20% des mouvements 8h.
# Sur données BTC 2017-2022 (train de référence), p80 |ret_8h| ≈ 2.8%.
# La fenêtre de non-retournement est 2× l'horizon (16 barres = 16h).
# SNR cost/std = 4.88% (vs 6.76% en 4h) — amélioration ~30%.
TRADEABLE_QUANTILE_LONG: float = 0.80  # top 20% — bon équilibre signal/volume
LONG_MIN_ABS_RETURN: float = 0.015    # 1.5% plancher — frais/mouvement < 7%
NON_REVERSAL_WINDOW_LONG: int = 16    # 2× horizon — détecte les retournements intra-8h
NON_REVERSAL_THRESHOLD_FACTOR_LONG: float = 0.30  # filtre strict : labels propres
GRAY_ZONE_FACTOR_LONG: float = 0.10
TARGET_REVERSAL_COL_LONG: str = "future_ret_h16_min"  # min(1h_ret[t+1..t+16])
REGIME_COL_LONG: str = "regime_long"

# SHORT — paramètres asymétriques, plus stricts
TRADEABLE_QUANTILE_SHORT: float = 0.84  # top 16% — plus d'opportunités short qualifiées
COST_SHORT_MULT: float = 1.5
NON_REVERSAL_WINDOW: int = 6   # réduit : moins de rejet anti-retournement
NON_REVERSAL_THRESHOLD_FACTOR: float = 0.45
GRAY_ZONE_FACTOR_SHORT: float = 0.18

# SHORT asymétrique v2 — utilisé par ai/level_0/short_labels.py
SHORT_HORIZON_BARS: int = 4          # horizon primaire (4 barres = 4h) — même que LONG
SHORT_ALT_HORIZON_BARS: int = 8      # horizon secondaire (8 barres = 8h)
SHORT_TRADEABLE_QUANTILE: float = 0.84   # top 16% — aligné TRADEABLE_QUANTILE_SHORT
SHORT_MIN_ABS_RETURN: float = 0.010      # plancher abaissé à 1.0% (cohérent avec 4h)
SHORT_COST_PCT: float = 0.0012           # coût round-trip short (funding inclus)
SHORT_SQUEEZE_LIMIT: float = 0.008       # seuil rejet squeeze légèrement assoupli
SHORT_GRAY_MULT: float = 1.12            # zone grise réduite
SHORT_NON_REVERSAL_WINDOW: int = 5       # réduit : 5 barres au lieu de 6
SHORT_LATE_ENTRY_ATR: float = 2.5        # entrée tardive si prix a déjà bougé > 2.5× ATR

# ─────────────────────────────────────────────────────────────────────────────
# FILTRE STAGE 1 — paramètres de calibration
# ─────────────────────────────────────────────────────────────────────────────
FILTER_BETA_LONG: float  = 1.5   # F-beta seuil long (recall-favoring)
FILTER_BETA_SHORT: float = 1.0   # F-beta seuil short (balanced)

# ─────────────────────────────────────────────────────────────────────────────
# PNL LABELS — coût round-trip pour les cibles de régression
# ─────────────────────────────────────────────────────────────────────────────
PNL_COST_MULT: int = 2  # multiplier sur COST_PCT pour le label PnL round-trip

# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL DE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
INITIAL_EQUITY: float = 10_000.0

# ─────────────────────────────────────────────────────────────────────────────
# SPLITS TEMPORELS
# ─────────────────────────────────────────────────────────────────────────────
TRAIN_END_YEAR: int = 2022   # train   : ≤ 2022
VAL_YEAR: int = 2023         # val     :   2023
TEST_FROM_YEAR: int = 2024   # test    : ≥ 2024

# ─────────────────────────────────────────────────────────────────────────────
# FEATURE ENGINEERING — colonnes cibles
# ─────────────────────────────────────────────────────────────────────────────
# TARGET_COL : log-rendement cumulé sur HORIZON_BARS barres en avant.
#   future_ret_8h[t] = log(Close[t+8]) − log(Close[t])
# TARGET_REVERSAL_COL / LONG : min/max des rendements 1h individuels sur les
#   16 barres suivant le signal (détection d'inversion intra-position, 2×horizon).
TARGET_COL: str = "future_ret_8h"
TARGET_REVERSAL_COL: str = "future_ret_h16_max"        # max(1h_ret[t+1..t+16])
TARGET_REVERSAL_COL_LONG: str = "future_ret_h16_min"   # min(1h_ret[t+1..t+16])
ATR_COL: str = "atr_14"
RV_COL: str = "rv_24"
CLOSE_COL: str = "Close"
DATETIME_COL: str = "datetime"
REGIME_COL: str = "regime_short"
REGIME_COL_LONG: str = "regime_long"


# ─────────────────────────────────────────────────────────────────────────────
# GARDE-FOU — empêche les incohérences d'horizon futures
# ─────────────────────────────────────────────────────────────────────────────

def assert_horizon(h: int, context: str = "") -> None:
    """
    Appeler dans tout composant qui dépend de l'horizon.
    Lève ValueError immédiatement si l'horizon est incohérent.

    Usage :
        from ai.level_0.constants import assert_horizon, HORIZON_BARS
        assert_horizon(HORIZON_BARS)
    """
    if h != HORIZON_BARS:
        raise ValueError(
            f"Horizon mismatch{' [' + context + ']' if context else ''}: "
            f"got {h}, expected {HORIZON_BARS} ({HORIZON_MINUTES} min). "
            f"Importer depuis level_0.constants — ne jamais hardcoder."
        )


def assert_feature_col(col: str, df_cols, context: str = "") -> None:
    """Vérifie qu'une colonne requise est présente dans le DataFrame."""
    if col not in df_cols:
        raise RuntimeError(
            f"Colonne requise '{col}' manquante{' [' + context + ']' if context else ''}. "
            f"Vérifier le CSV et le preprocessing."
        )
