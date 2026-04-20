"""
level_0/constants.py — SOURCE DE VÉRITÉ UNIQUE
===============================================

Tout composant qui a besoin d'un horizon, d'un coût, d'un split ou d'un
paramètre global DOIT importer depuis ce fichier.

Ne jamais hardcoder ces valeurs ailleurs dans le projet.
Si une valeur doit changer, elle change ici et seulement ici.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# HORIZON TEMPOREL — convention unique pour tout le pipeline
# ─────────────────────────────────────────────────────────────────────────────
# 1 barre h1 = 60 minutes.
# Tous les labels, modèles, backtests et inférences utilisent cet horizon.
# Si le CSV change de fréquence, mettre à jour ICI et re-calibrer.

HORIZON_BARS: int = 1          # nombre de barres forward (label target)
HORIZON_MINUTES: int = 60      # durée réelle en minutes
BAR_FREQUENCY: str = "1h"      # chaîne de fréquence pour les logs

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

# LONG — paramètres stricts (signal qualitatif, pas volume)
TRADEABLE_QUANTILE_LONG: float = 0.88  # top 12% → vise 7-8% positifs (vs 11% avant)
LONG_MIN_ABS_RETURN: float = 0.003     # plancher 0.3% pour barres 1h (ex 3% = daily)
NON_REVERSAL_WINDOW_LONG: int = 3      # fenêtre non-retournement long
NON_REVERSAL_THRESHOLD_FACTOR_LONG: float = 0.40  # reversal si prix dip > 40% du thr
GRAY_ZONE_FACTOR_LONG: float = 0.20   # zone grise long élargie (vs 0.15 avant)
TARGET_REVERSAL_COL_LONG: str = "future_ret_h3_min"  # min(ret[t+1..t+3])
REGIME_COL_LONG: str = "regime_long"

# SHORT — paramètres asymétriques, plus stricts
TRADEABLE_QUANTILE_SHORT: float = 0.82 # top 18% : moins de labels, plus propres
COST_SHORT_MULT: float = 1.5           # coût effectif short = 1.5x le coût long
                                        # (spread + funding + slippage sur recovery)
NON_REVERSAL_WINDOW: int = 3            # fenêtres de non-retournement short (barres)
NON_REVERSAL_THRESHOLD_FACTOR: float = 0.5  # le recovery ne doit pas dépasser thr*0.5
GRAY_ZONE_FACTOR_SHORT: float = 0.25   # zone grise short plus large — signal plus bruité

# ─────────────────────────────────────────────────────────────────────────────
# FILTRE STAGE 1 — paramètres de calibration
# ─────────────────────────────────────────────────────────────────────────────
# Centralisé ici pour éviter la duplication dans filter.py / train_pipeline.py.
FILTER_BETA_LONG: float  = 1.5   # F-beta seuil long (recall-favoring)
FILTER_BETA_SHORT: float = 1.0   # F-beta seuil short (balanced)

# ─────────────────────────────────────────────────────────────────────────────
# PNL LABELS — coût round-trip pour les cibles de régression
# ─────────────────────────────────────────────────────────────────────────────
# Un trade = entrée + sortie = 2 × fee.
# Toute fonction qui construit y_*_pnl DOIT utiliser cette constante.
# Un backtest qui utilise une autre définition produit des métriques incohérentes.
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
# FEATURE ENGINEERING — colonne source du label
# ─────────────────────────────────────────────────────────────────────────────
TARGET_COL: str = "future_ret_h"           # rendement forward 1 barre
TARGET_REVERSAL_COL: str = "future_ret_h3_max"  # max(ret[h+1..h+3]) — non-reversal short
ATR_COL: str = "atr_14"
RV_COL: str = "rv_24"                     # rv_24 existe dans le CSV (rv_60 N'EXISTE PAS)
CLOSE_COL: str = "Close"
DATETIME_COL: str = "datetime"
REGIME_COL: str = "regime_short"          # régime short (calculé dans le DataFrame)
REGIME_COL_LONG: str = "regime_long"      # régime long (calculé dans le DataFrame)


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
