"""
level_2/short_config.py — CONFIGURATION DU MODÈLE SHORT
========================================================

Le short est plus conservateur que le long à TOUS les niveaux :
  - régularisation plus forte (moins d'estimateurs, profondeur réduite)
  - seuils de décision plus élevés
  - validation inter-années obligatoire
  - TCN désactivé par défaut (signal trop fragile pour le justifier)

Asymétrie structurelle du marché crypto :
  - Les baisses sont plus abruptes mais moins prévisibles
  - La pression vendeuse est plus volatile que la pression acheteuse
  - Le funding rate et les liquidations introduisent des non-linéarités
    absentes du côté long
  - Les faux signaux short sont plus coûteux (re-hausse rapide)

Ces différences justifient une configuration indépendante.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ShortModelConfig:
    """Configuration du modèle SHORT — plus conservateur que le LONG."""

    # ── Reproductibilité ──────────────────────────────────────────────────────
    seed: int = 42

    # ── Logistic Regression ───────────────────────────────────────────────────
    lr_C: float = 0.05          # régularisation plus forte qu'en long (0.1)
    lr_max_iter: int = 2000
    lr_solver: str = "lbfgs"

    # ── XGBoost / HistGBT ─────────────────────────────────────────────────────
    xgb_n_estimators: int = 400         # moins qu'en long (600) → moins d'overfit
    xgb_max_depth: int = 3              # profondeur réduite → généralisation
    xgb_learning_rate: float = 0.03     # plus lent → plus robuste
    xgb_subsample: float = 0.70
    xgb_colsample_bytree: float = 0.60  # moins de features → diversité
    xgb_reg_alpha: float = 0.30         # L1 plus fort
    xgb_reg_lambda: float = 2.00        # L2 plus fort
    xgb_min_child_weight: int = 30      # nœuds plus stables

    # ── TCN — désactivé par défaut pour le short ──────────────────────────────
    tcn_enabled: bool = False
    tcn_lookback: int = 64
    tcn_d_model: int = 96        # plus petit qu'en long (128)
    tcn_n_layers: int = 3
    tcn_dropout: float = 0.15    # dropout plus élevé → régularisation
    tcn_epochs: int = 30
    tcn_batch_size: int = 128
    tcn_learning_rate: float = 2e-4
    tcn_patience: int = 6
    tcn_min_windows: int = 150

    # ── Critères d'acceptation — plus stricts qu'en long ─────────────────────
    min_macro_f1: float = 0.52
    min_auc: float = 0.62
    min_precision_positive: float = 0.12
    tcn_min_improvement: float = 0.02

    # ── Validation inter-années — OBLIGATOIRE pour le short ───────────────────
    require_yearly_stability: bool = True
    min_pf_per_year: float = 0.80       # PF minimum par année
    min_wr_per_year: float = 0.40       # WR minimum par année
    max_bad_years_allowed: int = 1      # au plus 1 année sous les seuils

    # ── Calibration ───────────────────────────────────────────────────────────
    calibration_method: str = "platt"
    calibration_beta: float = 1.0   # plus conservateur que long (1.5)
    filter_calibration_by_regime: bool = True
