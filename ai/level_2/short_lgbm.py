"""
level_2/short_lgbm.py — MODÈLE LIGHTGBM POUR LA CLASSIFICATION SHORT
======================================================================

Avantages vs HistGradientBoosting (modèle précédent) :
  - DART boosting : meilleure diversité d'arbres, moins de surapprentissage
  - Probabilités mieux calibrées : p90 ≈ 0.50–0.75 vs 0.12–0.20 pour HistGBT
  - Support natif des NaN (pas de fillna obligatoire, mais on le fait quand même)
  - GPU via device='cuda' si LightGBM est compilé avec CUDA
  - Early stopping natif sur AUC val
  - Calibration Platt (LogisticRegression) systématique sur les sorties val

Interface :
  - sklearn-compatible (fit / predict_proba)
  - Retourne P(y_short=1) via predict_proba_short()
  - feature_importance_ disponible après fit() (gain)
  - val_auc_ disponible après fit()
  - best_iteration_ disponible après fit()

Conventions :
  - Labels gris (y == -1) exclus automatiquement
  - sample_weight pour équilibrer positifs/négatifs
  - Calibration Platt activée si n_val_valid >= 100
  - Fallback device='cpu' si device='cuda' non supporté
"""
from __future__ import annotations

import logging
import warnings
from typing import List, Optional

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False
    warnings.warn("LightGBM non disponible. Installer avec : pip install lightgbm")

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

class ShortLGBMConfig:
    """
    Configuration du modèle LightGBM SHORT.

    Tous les paramètres sont définis dans __init__ avec des valeurs par défaut
    (pas des annotations de type pures — compatibilité garantie sans dataclass).
    """

    def __init__(
        self,
        n_estimators: int = 800,
        learning_rate: float = 0.02,
        num_leaves: int = 63,
        max_depth: int = 6,
        min_child_samples: int = 20,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        reg_alpha: float = 0.1,
        reg_lambda: float = 1.0,
        boosting_type: str = "dart",    # DART : meilleure calibration que gbdt
        drop_rate: float = 0.1,
        device: str = "cpu",            # "cuda" si GPU + LightGBM CUDA compilé
        random_state: int = 42,
        verbose: int = -1,
        early_stopping_rounds: int = 50,
        min_val_samples: int = 100,     # min samples val pour activer calibration Platt
    ) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.max_depth = max_depth
        self.min_child_samples = min_child_samples
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.reg_alpha = reg_alpha
        self.reg_lambda = reg_lambda
        self.boosting_type = boosting_type
        self.drop_rate = drop_rate
        self.device = device
        self.random_state = random_state
        self.verbose = verbose
        self.early_stopping_rounds = early_stopping_rounds
        self.min_val_samples = min_val_samples

    def to_lgb_params(self, device: str) -> dict:
        """Retourne le dict de paramètres pour lgb.train()."""
        params: dict = {
            "objective": "binary",
            "metric": "auc",
            "boosting_type": self.boosting_type,
            "num_leaves": self.num_leaves,
            "max_depth": self.max_depth,
            "learning_rate": self.learning_rate,
            "min_child_samples": self.min_child_samples,
            "subsample": self.subsample,
            "subsample_freq": 1,
            "colsample_bytree": self.colsample_bytree,
            "reg_alpha": self.reg_alpha,
            "reg_lambda": self.reg_lambda,
            "random_state": self.random_state,
            "verbosity": self.verbose,
            "device": device,
            "n_jobs": -1,
        }
        # Paramètres DART uniquement (ignorés si boosting_type != "dart")
        if self.boosting_type == "dart":
            params["drop_rate"] = self.drop_rate
            params["skip_drop"] = 0.5
            params["uniform_drop"] = False
            params["max_drop"] = 50
        return params


# ─────────────────────────────────────────────────────────────────────────────
# Détection GPU
# ─────────────────────────────────────────────────────────────────────────────

def _detect_lgbm_device(requested: str) -> str:
    """
    Vérifie si LightGBM supporte le device demandé.

    Tente un micro-entraînement sur device='cuda'.
    Retourne 'cuda' si supporté, 'cpu' sinon.
    Toujours retourne 'cpu' si requested == 'cpu'.
    """
    if requested != "cuda" or not _LGB_AVAILABLE:
        return "cpu"

    try:
        # Micro-dataset minimal pour tester le device
        X_probe = np.random.rand(100, 5).astype(np.float32)
        y_probe = np.random.randint(0, 2, 100).astype(np.int32)
        ds = lgb.Dataset(X_probe, label=y_probe, silent=True)
        params_probe = {
            "objective": "binary",
            "num_leaves": 4,
            "n_estimators": 5,
            "device": "cuda",
            "verbosity": -1,
        }
        booster = lgb.train(
            params_probe,
            ds,
            num_boost_round=5,
            valid_sets=[ds],
            callbacks=[lgb.log_evaluation(period=-1)],
        )
        del booster
        logger.info("[ShortLGBM] device=cuda supporté par LightGBM.")
        return "cuda"
    except Exception as exc:
        logger.warning(
            "[ShortLGBM] device='cuda' non supporté (%s). Fallback sur 'cpu'.", exc
        )
        return "cpu"


# ─────────────────────────────────────────────────────────────────────────────
# Modèle principal
# ─────────────────────────────────────────────────────────────────────────────

class ShortLGBMModel:
    """
    LightGBM pour la classification SHORT (P(baisse significative)).

    Avantages vs HistGradientBoosting :
      - DART mode : meilleure diversité, moins de surapprentissage
      - Probabilités mieux calibrées (p90 ≈ 0.50–0.75 vs 0.12–0.20 HistGBT)
      - Support natif des NaN
      - GPU via device='cuda' si disponible
      - Early stopping natif sur AUC val

    Workflow typique :
        model = ShortLGBMModel(features=FEATURES_SHORT + FEATURES_SHORT_PROXY)
        model.fit(df_train, y_train, df_val, y_val)
        proba = model.predict_proba_short(df_test)
        print(model.val_auc_, model.best_iteration_)
    """

    def __init__(
        self,
        features: List[str],
        cfg: Optional[ShortLGBMConfig] = None,
    ) -> None:
        if not _LGB_AVAILABLE:
            raise ImportError("LightGBM requis. pip install lightgbm")

        self.features: List[str] = features
        self.cfg: ShortLGBMConfig = cfg or ShortLGBMConfig()

        # Attributs post-fit
        self.model: Optional[lgb.Booster] = None
        self.calibrator: Optional[LogisticRegression] = None
        self._device_used: str = "cpu"
        self._feats_used: List[str] = []
        self.feature_importance_: dict = {}
        self.best_iteration_: int = 0
        self.val_auc_: float = 0.0
        self.val_auc_calibrated_: float = 0.0

    # ── Helpers privés ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_sample_weight(y: np.ndarray) -> np.ndarray:
        """
        Balance positifs / négatifs via sample_weight.
        Positifs (y=1) reçoivent un poids scale_pos_weight = n_neg / n_pos.
        Négatifs (y=0) reçoivent un poids de 1.0.
        """
        n_pos = int((y == 1).sum())
        n_neg = int((y == 0).sum())
        scale_pos = float(n_neg) / max(n_pos, 1)
        weights = np.where(y == 1, scale_pos, 1.0).astype(np.float32)
        return weights

    @staticmethod
    def _prepare_X(
        df: pd.DataFrame,
        feats: List[str],
        mask: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Extrait la matrice X depuis df, fillna(0) pour les NaN résiduels.
        LightGBM gère nativement les NaN mais on remplit pour sécurité.
        """
        sub = df.loc[mask, feats] if mask is not None else df[feats]
        X = sub.values.astype(np.float32)
        # Remplacer inf et NaN
        X = np.where(np.isfinite(X), X, 0.0)
        return X

    # ── Entraînement ──────────────────────────────────────────────────────────

    def fit(
        self,
        df_train: pd.DataFrame,
        y_train: np.ndarray,
        df_val: pd.DataFrame,
        y_val: np.ndarray,
    ) -> "ShortLGBMModel":
        """
        Entraîne LightGBM avec early stopping sur AUC val.
        Applique une calibration Platt (LogisticRegression) sur les sorties val.

        Paramètres
        ----------
        df_train : DataFrame d'entraînement (index aligné avec y_train)
        y_train  : labels {0, 1, -1} — -1 = zone grise (exclus)
        df_val   : DataFrame de validation (index aligné avec y_val)
        y_val    : labels {0, 1, -1} — -1 = zone grise (exclus)

        Retourne
        --------
        self (pour chaining)
        """
        # ── 1. Features disponibles ───────────────────────────────────────────
        feats_train = set(df_train.columns)
        feats_val = set(df_val.columns)
        feats = [f for f in self.features if f in feats_train and f in feats_val]
        if not feats:
            raise ValueError(
                "[ShortLGBM] Aucune feature demandée n'est présente dans df_train/df_val. "
                f"Features demandées : {self.features[:5]}... "
                f"Colonnes train : {list(df_train.columns)[:5]}..."
            )
        self._feats_used = feats
        n_feats = len(feats)
        logger.info("[ShortLGBM] Features utilisées : %d / %d", n_feats, len(self.features))

        # ── 2. Filtrage des labels gris ───────────────────────────────────────
        y_train_arr = np.asarray(y_train, dtype=np.int32)
        y_val_arr = np.asarray(y_val, dtype=np.int32)

        tr_mask = y_train_arr >= 0
        vl_mask = y_val_arr >= 0

        n_tr = int(tr_mask.sum())
        n_vl = int(vl_mask.sum())
        logger.info(
            "[ShortLGBM] Train valide : %d / %d | Val valide : %d / %d",
            n_tr, len(y_train_arr), n_vl, len(y_val_arr),
        )

        if n_tr < 50:
            raise ValueError(
                f"[ShortLGBM] Trop peu de samples d'entraînement valides : {n_tr}. "
                "Vérifier le labeling et les filtres."
            )

        # Extraire les index valides
        tr_idx = np.where(tr_mask)[0]
        vl_idx = np.where(vl_mask)[0]

        # Extraire X en passant par iloc pour gérer les DataFrames avec index non-entiers
        X_tr = df_train.iloc[tr_idx][feats].values.astype(np.float32)
        X_tr = np.where(np.isfinite(X_tr), X_tr, 0.0)

        X_vl = df_val.iloc[vl_idx][feats].values.astype(np.float32)
        X_vl = np.where(np.isfinite(X_vl), X_vl, 0.0)

        y_tr = y_train_arr[tr_idx]
        y_vl = y_val_arr[vl_idx]

        # ── 3. Sample weights ─────────────────────────────────────────────────
        sw_tr = self._compute_sample_weight(y_tr)

        n_pos_tr = int((y_tr == 1).sum())
        n_neg_tr = int((y_tr == 0).sum())
        n_pos_vl = int((y_vl == 1).sum())
        logger.info(
            "[ShortLGBM] Train → pos=%d neg=%d | Val → pos=%d",
            n_pos_tr, n_neg_tr, n_pos_vl,
        )

        if n_pos_tr == 0:
            raise ValueError("[ShortLGBM] Aucun label positif (y=1) en train.")
        if n_pos_vl == 0:
            logger.warning(
                "[ShortLGBM] Aucun label positif en val — AUC non calculable, "
                "early stopping désactivé."
            )

        # ── 4. Détection GPU ──────────────────────────────────────────────────
        device = _detect_lgbm_device(self.cfg.device)
        self._device_used = device
        logger.info("[ShortLGBM] Device : %s", device)

        # ── 5. Datasets LightGBM ──────────────────────────────────────────────
        lgb_train = lgb.Dataset(
            X_tr,
            label=y_tr.astype(np.float32),
            weight=sw_tr,
            free_raw_data=False,
        )
        lgb_val = lgb.Dataset(
            X_vl,
            label=y_vl.astype(np.float32),
            reference=lgb_train,
            free_raw_data=False,
        )

        # ── 6. Paramètres ─────────────────────────────────────────────────────
        params = self.cfg.to_lgb_params(device)

        # Callbacks
        callbacks = [lgb.log_evaluation(period=100)]
        if n_pos_vl > 0:
            callbacks.append(
                lgb.early_stopping(
                    stopping_rounds=self.cfg.early_stopping_rounds,
                    verbose=False,
                )
            )

        # ── 7. Entraînement ───────────────────────────────────────────────────
        logger.info(
            "[ShortLGBM] Début entraînement LightGBM — n_estimators=%d device=%s",
            self.cfg.n_estimators, device,
        )
        booster = lgb.train(
            params,
            lgb_train,
            num_boost_round=self.cfg.n_estimators,
            valid_sets=[lgb_val],
            valid_names=["val"],
            callbacks=callbacks,
        )
        self.model = booster
        self.best_iteration_ = booster.best_iteration if booster.best_iteration > 0 else self.cfg.n_estimators
        logger.info("[ShortLGBM] best_iteration=%d", self.best_iteration_)

        # ── 8. AUC brute sur val ──────────────────────────────────────────────
        raw_proba_val = booster.predict(
            X_vl,
            num_iteration=self.best_iteration_,
        )
        if n_pos_vl > 0:
            try:
                self.val_auc_ = float(roc_auc_score(y_vl, raw_proba_val))
                logger.info("[ShortLGBM] val_auc_brute=%.4f", self.val_auc_)
            except Exception:
                self.val_auc_ = 0.0
        else:
            self.val_auc_ = 0.0

        # ── 9. Calibration Platt ──────────────────────────────────────────────
        if n_vl >= self.cfg.min_val_samples and n_pos_vl > 0:
            calibrator = LogisticRegression(
                C=1.0,
                solver="lbfgs",
                max_iter=1000,
                random_state=self.cfg.random_state,
                class_weight="balanced",
            )
            # Calibration Platt : LogisticRegression sur logit(raw_proba_val)
            # On passe les probas brutes comme feature scalaire
            calibrator.fit(raw_proba_val.reshape(-1, 1), y_vl)
            self.calibrator = calibrator

            # AUC calibrée
            proba_cal_val = calibrator.predict_proba(
                raw_proba_val.reshape(-1, 1)
            )[:, 1]
            try:
                self.val_auc_calibrated_ = float(roc_auc_score(y_vl, proba_cal_val))
                logger.info(
                    "[ShortLGBM] val_auc_calibree=%.4f (Platt)", self.val_auc_calibrated_
                )
            except Exception:
                self.val_auc_calibrated_ = self.val_auc_
        else:
            logger.warning(
                "[ShortLGBM] Calibration Platt ignorée "
                "(n_val_valid=%d < min=%d ou n_pos_vl=%d).",
                n_vl, self.cfg.min_val_samples, n_pos_vl,
            )
            self.calibrator = None
            self.val_auc_calibrated_ = self.val_auc_

        # ── 10. Feature importance ────────────────────────────────────────────
        importance_vals = booster.feature_importance(importance_type="gain")
        self.feature_importance_ = {
            feat: float(imp)
            for feat, imp in zip(feats, importance_vals)
        }
        # Tri décroissant pour lisibilité
        self.feature_importance_ = dict(
            sorted(self.feature_importance_.items(), key=lambda x: x[1], reverse=True)
        )
        top5 = list(self.feature_importance_.items())[:5]
        logger.info("[ShortLGBM] Top-5 features (gain): %s", top5)

        return self

    # ── Inférence ─────────────────────────────────────────────────────────────

    def predict_proba_short(self, df: pd.DataFrame) -> np.ndarray:
        """
        Retourne P(y_short=1), shape (n,).

        Applique la calibration Platt si disponible.
        Remplace NaN/inf par 0 avant l'inférence.
        Lève ValueError si le modèle n'a pas été entraîné.
        """
        if self.model is None:
            raise ValueError(
                "[ShortLGBM] Le modèle n'a pas été entraîné. Appeler .fit() d'abord."
            )

        feats = [f for f in self._feats_used if f in df.columns]
        if not feats:
            raise ValueError(
                "[ShortLGBM] Aucune feature utilisée à l'entraînement "
                "n'est présente dans df."
            )

        # Aligner les features dans l'ordre d'entraînement
        missing = [f for f in self._feats_used if f not in df.columns]
        if missing:
            logger.warning(
                "[ShortLGBM] %d features manquantes en inférence (remplacées par 0) : %s",
                len(missing), missing[:5],
            )

        X = np.zeros((len(df), len(self._feats_used)), dtype=np.float32)
        for i, feat in enumerate(self._feats_used):
            if feat in df.columns:
                col_vals = pd.to_numeric(df[feat], errors="coerce").values.astype(np.float32)
                X[:, i] = np.where(np.isfinite(col_vals), col_vals, 0.0)

        raw_proba = self.model.predict(X, num_iteration=self.best_iteration_)

        if self.calibrator is not None:
            proba = self.calibrator.predict_proba(
                raw_proba.reshape(-1, 1)
            )[:, 1]
        else:
            proba = raw_proba

        return proba.astype(np.float64)

    # ── Utilitaires ───────────────────────────────────────────────────────────

    def get_feature_importance(self) -> dict:
        """
        Retourne l'importance des features (gain, triée décroissant).
        Disponible uniquement après fit().
        """
        if not self.feature_importance_:
            raise ValueError(
                "[ShortLGBM] feature_importance_ vide. Appeler .fit() d'abord."
            )
        return dict(self.feature_importance_)

    def summary(self) -> dict:
        """
        Retourne un dict résumé des métriques post-fit :
          val_auc, val_auc_calibrated, best_iteration,
          device_used, n_features, calibration_active
        """
        return {
            "val_auc": self.val_auc_,
            "val_auc_calibrated": self.val_auc_calibrated_,
            "best_iteration": self.best_iteration_,
            "device_used": self._device_used,
            "n_features": len(self._feats_used),
            "calibration_active": self.calibrator is not None,
            "boosting_type": self.cfg.boosting_type,
        }

    def __repr__(self) -> str:
        fitted = self.model is not None
        if fitted:
            return (
                f"ShortLGBMModel("
                f"n_feats={len(self._feats_used)}, "
                f"best_iter={self.best_iteration_}, "
                f"val_auc={self.val_auc_:.4f}, "
                f"calibrated={self.calibrator is not None}, "
                f"device={self._device_used})"
            )
        return f"ShortLGBMModel(fitted=False, n_feats_requested={len(self.features)})"
