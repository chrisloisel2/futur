"""
level_2/long_config.py — CONFIGURATION DU MODÈLE LONG
======================================================

Tous les hyperparamètres du pipeline LONG sont ici.
Ne pas disperser les hyperparamètres dans le code — centraliser dans la config.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class LongModelConfig:
    """Configuration complète du modèle directionnel LONG."""

    # ── Reproductibilité ──────────────────────────────────────────────────────
    seed: int = 42

    # ── Logistic Regression (baseline A) ─────────────────────────────────────
    lr_C: float = 0.1
    lr_max_iter: int = 2000
    lr_solver: str = "lbfgs"

    # ── XGBoost / HistGBT (baseline B) ───────────────────────────────────────
    xgb_n_estimators: int = 600
    xgb_max_depth: int = 4
    xgb_learning_rate: float = 0.04
    xgb_subsample: float = 0.75
    xgb_colsample_bytree: float = 0.70
    xgb_reg_alpha: float = 0.10      # régularisation L1
    xgb_reg_lambda: float = 1.00     # régularisation L2
    xgb_min_child_weight: int = 20   # stabilité financière

    # ── TCN (modèle séquentiel) ───────────────────────────────────────────────
    tcn_lookback: int = 64           # nombre de barres en contexte
    tcn_d_model: int = 128
    tcn_n_layers: int = 4
    tcn_dropout: float = 0.10
    tcn_epochs: int = 40
    tcn_batch_size: int = 128
    tcn_learning_rate: float = 3e-4
    tcn_patience: int = 7            # early stopping
    tcn_min_windows: int = 200       # minimum de fenêtres pour entraîner le TCN

    # ── Critères d'acceptation ────────────────────────────────────────────────
    min_macro_f1: float = 0.54       # en dessous → modèle rejeté
    min_auc: float = 0.62            # en dessous → modèle rejeté
    min_precision_positive: float = 0.15  # en dessous → signal trop bruité
    tcn_min_improvement: float = 0.02     # TCN doit battre tabular de +0.02 F1

    # ── Calibration ───────────────────────────────────────────────────────────
    calibration_method: str = "isotonic"  # "isotonic" ou "platt"
    calibration_beta: float = 1.5         # F-beta pour sélection de seuil direction
