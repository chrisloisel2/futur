"""
ai/meta/ood_detector.py — Out-of-Distribution Detector

Détecte les barres qui sont structurellement différentes des données d'entraînement.
Quand le modèle voit un régime jamais vu → la prédiction n'est pas fiable.

Méthode: Distance de Mahalanobis
  d(x) = sqrt((x - μ)ᵀ Σ⁻¹ (x - μ))
  Calibré sur le training set → seuil = P95 des distances

Fallback si Σ est singulière: distance L2 normalisée par feature stddev.

Usage:
  det = OODDetector()
  det.fit(X_train)
  score  = det.score(x_new)       # distance normalisée [0, +∞)
  is_ood = det.is_ood(x_new)      # bool (> P95 threshold)
  pct    = det.percentile(score)  # rang dans la distribution de training
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np


class OODDetector:
    """
    Détecteur OOD basé sur la distance de Mahalanobis.

    Le seuil est automatiquement calibré sur la distribution des distances
    du training set (percentile configurable, défaut P95).
    """

    def __init__(self, threshold_pct: float = 95.0):
        self._thr_pct      = threshold_pct
        self._mu:    Optional[np.ndarray] = None
        self._inv_cov: Optional[np.ndarray] = None
        self._std:   Optional[np.ndarray] = None   # fallback
        self._threshold: float = float("inf")
        self._train_scores: Optional[np.ndarray] = None
        self._fitted = False

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------

    def fit(self, X: np.ndarray) -> "OODDetector":
        X = np.array(X, dtype=float)
        X = X[~np.any(np.isnan(X), axis=1)]    # remove NaN rows

        self._mu  = X.mean(axis=0)
        self._std = X.std(axis=0).clip(1e-8)

        # Essayer la covariance complète
        try:
            cov = np.cov(X, rowvar=False)
            # Régularisation de Tikhonov pour éviter la singularité
            reg = 1e-4 * np.trace(cov) / cov.shape[0]
            cov_reg = cov + reg * np.eye(cov.shape[0])
            self._inv_cov = np.linalg.inv(cov_reg)
        except np.linalg.LinAlgError:
            self._inv_cov = None

        # Calibrage du seuil sur le training set
        self._train_scores = self._compute_scores(X)
        self._threshold = float(np.percentile(self._train_scores, self._thr_pct))
        self._fitted = True
        return self

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def score(self, x: np.ndarray) -> float:
        """Distance de Mahalanobis normalisée (0 = typique, +∞ = très OOD)."""
        if not self._fitted:
            raise RuntimeError("Call fit() first")
        x = np.array(x, dtype=float).flatten()
        x = np.where(np.isnan(x), self._mu, x)   # impute NaN
        return float(self._mahal(x))

    def score_batch(self, X: np.ndarray) -> np.ndarray:
        X = np.array(X, dtype=float)
        scores = np.zeros(len(X))
        for i, row in enumerate(X):
            row = np.where(np.isnan(row), self._mu, row)
            scores[i] = self._mahal(row)
        return scores

    def is_ood(self, x: np.ndarray, threshold: Optional[float] = None) -> bool:
        thr = threshold if threshold is not None else self._threshold
        return self.score(x) > thr

    def percentile(self, score: float) -> float:
        """Rang du score dans la distribution d'entraînement (0-100)."""
        if self._train_scores is None:
            return 50.0
        return float(np.mean(self._train_scores <= score) * 100)

    @property
    def threshold(self) -> float:
        return self._threshold

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        np.save(path / "mu.npy",    self._mu)
        np.save(path / "std.npy",   self._std)
        np.save(path / "scores.npy", self._train_scores)
        if self._inv_cov is not None:
            np.save(path / "inv_cov.npy", self._inv_cov)
        (path / "meta.json").write_text(json.dumps({
            "threshold":     self._threshold,
            "threshold_pct": self._thr_pct,
            "has_inv_cov":   self._inv_cov is not None,
        }))

    def load(self, path: Path) -> None:
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        self._mu            = np.load(path / "mu.npy")
        self._std           = np.load(path / "std.npy")
        self._train_scores  = np.load(path / "scores.npy")
        self._threshold     = meta["threshold"]
        self._thr_pct       = meta["threshold_pct"]
        if meta.get("has_inv_cov") and (path / "inv_cov.npy").exists():
            self._inv_cov = np.load(path / "inv_cov.npy")
        self._fitted = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _mahal(self, x: np.ndarray) -> float:
        diff = x - self._mu
        if self._inv_cov is not None:
            d2 = float(diff @ self._inv_cov @ diff)
            return float(np.sqrt(max(0.0, d2)))
        # Fallback: L2 normalisé par std
        return float(np.sqrt(np.sum((diff / self._std) ** 2)))

    def _compute_scores(self, X: np.ndarray) -> np.ndarray:
        return np.array([self._mahal(row) for row in X])
