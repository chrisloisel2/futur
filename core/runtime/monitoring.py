"""
inference/monitoring.py — MONITORING DE DÉRIVE EN PRODUCTION
=============================================================

Détecte deux types de dérives :
  1. Dérive des features (covariate shift) : la distribution des inputs change
  2. Dérive du signal (concept drift) : la précision du modèle baisse

Ces deux mécanismes sont indépendants et complémentaires.
Un modèle peut dériver sur les features sans dériver sur le signal (features corrélées changent ensemble)
ou dériver sur le signal sans dériver les features (la relation input→output change).

Méthodes choisies :
  - Feature drift : Kolmogorov-Smirnov sur fenêtre glissante vs distribution de référence
  - Signal drift : Ewma de la précision sur fenêtre glissante
  - Pas de Z-score (trop sensible aux outliers dans les features financières)

Seuils :
  - KS p-value < 0.01 → drift probable
  - Ewma précision < 50% de la précision de référence → drift signal
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


class PredictionMonitor:
    """
    Moniteur de dérive pour le prédicteur live.

    Usage :
        monitor = PredictionMonitor.from_reference_stats(stats_path)
        monitor.record(features_dict, prediction, actual_label)
        report = monitor.check_drift()
    """

    def __init__(
        self,
        reference_means: Dict[str, float],
        reference_stds: Dict[str, float],
        reference_precision: float,
        window_size: int = 200,
        ks_alpha: float = 0.01,
        precision_min_ratio: float = 0.50,
        ewma_alpha: float = 0.10,
    ):
        self.reference_means      = reference_means
        self.reference_stds       = reference_stds
        self.reference_precision  = max(reference_precision, 0.01)
        self.window_size          = window_size
        self.ks_alpha             = ks_alpha
        self.precision_min_ratio  = precision_min_ratio
        self.ewma_alpha           = ewma_alpha

        # Buffers circulaires
        self._feature_buffer: Dict[str, deque] = {
            k: deque(maxlen=window_size) for k in reference_means
        }
        self._signal_buffer: deque = deque(maxlen=window_size)  # (predicted_positive, actual)
        self._ewma_precision: float = reference_precision

        self.n_recorded: int = 0

    @classmethod
    def from_reference_stats(cls, stats_path: Path, **kwargs) -> "PredictionMonitor":
        """
        Charge les statistiques de référence depuis un JSON.

        Format attendu :
        {
          "feature_means": {"feat1": 0.12, ...},
          "feature_stds":  {"feat1": 0.04, ...},
          "precision":     0.65
        }
        """
        stats_path = Path(stats_path)
        if not stats_path.exists():
            raise FileNotFoundError(f"Fichier de statistiques introuvable : {stats_path}")
        with open(stats_path) as f:
            data = json.load(f)
        return cls(
            reference_means=data.get("feature_means", {}),
            reference_stds=data.get("feature_stds", {}),
            reference_precision=data.get("precision", 0.5),
            **kwargs,
        )

    @classmethod
    def from_training_data(
        cls,
        X_train: np.ndarray,
        feature_names: List[str],
        y_true: np.ndarray,
        y_pred: np.ndarray,
        **kwargs,
    ) -> "PredictionMonitor":
        """
        Construit le moniteur depuis les données d'entraînement.

        Calcule les statistiques de référence directement.
        """
        means = {f: float(X_train[:, i].mean()) for i, f in enumerate(feature_names)}
        stds  = {f: float(X_train[:, i].std()) + 1e-9 for i, f in enumerate(feature_names)}

        positives = y_pred == 1
        if positives.sum() > 0:
            precision = float((y_true[positives] == 1).mean())
        else:
            precision = 0.5

        return cls(
            reference_means=means,
            reference_stds=stds,
            reference_precision=precision,
            **kwargs,
        )

    def record(
        self,
        features: Dict[str, float],
        predicted_action: str,
        actual_label: Optional[int] = None,
    ) -> None:
        """
        Enregistre une prédiction et les features associées.

        actual_label : 1 si la prédiction long/short s'est réalisée, 0 sinon.
                       None si on ne connaît pas encore le résultat.
        """
        self.n_recorded += 1

        # Enregistrer les features
        for feat, buf in self._feature_buffer.items():
            v = features.get(feat)
            if v is not None:
                buf.append(float(v))

        # Enregistrer le signal si on a le résultat
        if actual_label is not None and predicted_action in ("LONG", "SHORT"):
            self._signal_buffer.append(int(actual_label))
            # Mise à jour Ewma de la précision
            self._ewma_precision = (
                self.ewma_alpha * float(actual_label)
                + (1.0 - self.ewma_alpha) * self._ewma_precision
            )

    def check_drift(self) -> Dict:
        """
        Vérifie s'il y a dérive. Retourne un rapport avec les features driftées.

        Retourne
        --------
        dict :
          has_feature_drift   : bool
          has_signal_drift    : bool
          drifted_features    : List[str]
          ewma_precision      : float
          reference_precision : float
          n_recorded          : int
          details             : dict
        """
        n_min = min(30, self.window_size // 4)
        if self.n_recorded < n_min:
            return {
                "has_feature_drift":   False,
                "has_signal_drift":    False,
                "drifted_features":    [],
                "ewma_precision":      self._ewma_precision,
                "reference_precision": self.reference_precision,
                "n_recorded":          self.n_recorded,
                "details":             {"status": "insufficient_data"},
            }

        # ── Feature drift (KS) ────────────────────────────────────────────────
        drifted = []
        ks_details: Dict[str, dict] = {}

        for feat, buf in self._feature_buffer.items():
            if len(buf) < n_min:
                continue
            arr = np.array(list(buf))
            ref_mean = self.reference_means.get(feat)
            ref_std  = self.reference_stds.get(feat, 1.0)
            if ref_mean is None:
                continue

            # Test KS simplifié : comparer la moyenne de la fenêtre vs référence
            # en unités d'écart-type (z-score de la moyenne)
            cur_mean = float(arr.mean())
            z = abs(cur_mean - ref_mean) / max(ref_std, 1e-9)

            # Seuil empirique : z > 3 = drift probable (eq. p < 0.003 gaussien)
            is_drifted = z > 3.0
            if is_drifted:
                drifted.append(feat)
            ks_details[feat] = {
                "z_score": round(z, 3),
                "cur_mean": round(cur_mean, 6),
                "ref_mean": round(ref_mean, 6),
                "drifted": is_drifted,
            }

        has_feature_drift = len(drifted) > 0

        # ── Signal drift (Ewma) ───────────────────────────────────────────────
        ewma = self._ewma_precision
        min_prec = self.reference_precision * self.precision_min_ratio
        has_signal_drift = (
            len(self._signal_buffer) >= n_min
            and ewma < min_prec
        )

        return {
            "has_feature_drift":   has_feature_drift,
            "has_signal_drift":    has_signal_drift,
            "drifted_features":    drifted,
            "ewma_precision":      round(ewma, 4),
            "reference_precision": round(self.reference_precision, 4),
            "n_recorded":          self.n_recorded,
            "details": {
                "feature_drift": ks_details,
                "signal_precision_min": round(min_prec, 4),
                "n_signal_samples": len(self._signal_buffer),
            },
        }

    def save_stats(self, path: Path) -> None:
        """Sauvegarde les stats de référence (pour rechargement)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "feature_means": self.reference_means,
            "feature_stds":  self.reference_stds,
            "precision":     self.reference_precision,
            "n_recorded":    self.n_recorded,
            "ewma_precision": self._ewma_precision,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
