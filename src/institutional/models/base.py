"""
src/institutional/models/base.py
─────────────────────────────────────────────────────────────────────────────
Classe de base pour tous les modèles institutionnels.

Garanties :
  - walk-forward aware (fit/predict séparés)
  - model card automatique
  - sauvegarde/chargement avec versioning
  - feature importance
  - calibration probabiliste en option
"""
from __future__ import annotations

import abc
import json
import logging
import pickle
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional" / "models"


@dataclass
class ModelCard:
    """Carte d'identité du modèle — générée automatiquement après entraînement."""
    model_type: str
    version: str
    asset: str
    target: str
    train_period: Dict[str, str]
    n_train: int
    n_features: int
    feature_names: List[str]
    hyperparams: Dict[str, Any]
    train_metrics: Dict[str, float]
    validation_metrics: Dict[str, float]
    feature_importance: Dict[str, float] = field(default_factory=dict)
    calibration_method: Optional[str] = None
    notes: str = ""

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, default=str))

    def to_markdown(self) -> str:
        lines = [
            f"# Model Card — {self.model_type} {self.version}",
            f"",
            f"**Asset** : {self.asset}",
            f"**Target** : {self.target}",
            f"**Train period** : {self.train_period}",
            f"**N train** : {self.n_train}",
            f"**N features** : {self.n_features}",
            f"",
            f"## Hyperparameters",
            f"```json",
            json.dumps(self.hyperparams, indent=2),
            f"```",
            f"",
            f"## Train metrics",
        ]
        for k, v in self.train_metrics.items():
            lines.append(f"- {k}: {v:.4f}")
        lines.append(f"")
        lines.append(f"## Validation metrics")
        for k, v in self.validation_metrics.items():
            lines.append(f"- {k}: {v:.4f}")

        if self.feature_importance:
            lines.extend([f"", f"## Top 10 features"])
            top10 = sorted(self.feature_importance.items(), key=lambda x: -x[1])[:10]
            for name, imp in top10:
                lines.append(f"- {name}: {imp:.4f}")

        return "\n".join(lines)


class InstitutionalModel(abc.ABC):
    """
    Classe de base abstraite pour tous les modèles institutionnels.

    Sous-classes obligatoires : LightGBMModel, RidgeModel, etc.
    """

    def __init__(
        self,
        version: str = "v1.0",
        asset: str = "unknown",
        target: str = "tb_label",
    ):
        self.version = version
        self.asset = asset
        self.target = target
        self._fitted = False
        self._feature_names: List[str] = []
        self.card: Optional[ModelCard] = None

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @abc.abstractmethod
    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "InstitutionalModel":
        """Entraîne le modèle. Ne jamais appeler fit sur des données test."""
        ...

    @abc.abstractmethod
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Retourne les probabilités calibrées ∈ [0, 1] par classe."""
        ...

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Retourne la classe prédite (argmax)."""
        proba = self.predict_proba(X)
        if proba.ndim == 2:
            return proba.argmax(axis=1)
        return (proba > 0.5).astype(int)

    def predict_series(self, X: pd.DataFrame) -> pd.Series:
        """predict avec index préservé."""
        return pd.Series(self.predict(X), index=X.index)

    def predict_proba_df(self, X: pd.DataFrame) -> pd.DataFrame:
        """predict_proba avec index préservé."""
        proba = self.predict_proba(X)
        if proba.ndim == 1:
            return pd.DataFrame({"proba": proba}, index=X.index)
        return pd.DataFrame(
            proba,
            index=X.index,
            columns=[f"proba_class_{i}" for i in range(proba.shape[1])],
        )

    def feature_importance(self) -> Dict[str, float]:
        """Retourne les importances de features (si disponibles)."""
        return {}

    def generate_card(
        self,
        train_metrics: Dict[str, float],
        validation_metrics: Dict[str, float],
        train_period: Optional[Dict[str, str]] = None,
        n_train: int = 0,
    ) -> ModelCard:
        self.card = ModelCard(
            model_type=self.__class__.__name__,
            version=self.version,
            asset=self.asset,
            target=self.target,
            train_period=train_period or {},
            n_train=n_train,
            n_features=len(self._feature_names),
            feature_names=self._feature_names,
            hyperparams=self._get_params(),
            train_metrics=train_metrics,
            validation_metrics=validation_metrics,
            feature_importance=self.feature_importance(),
        )
        return self.card

    @abc.abstractmethod
    def _get_params(self) -> Dict[str, Any]:
        ...

    def save(self, path: Optional[Path] = None) -> Path:
        if path is None:
            path = (
                ARTIFACTS_ROOT
                / self.__class__.__name__.lower()
                / self.asset
                / f"{self.version}.pkl"
            )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info(f"Model saved: {path}")

        if self.card is not None:
            card_path = path.with_suffix(".card.json")
            self.card.save(card_path)
            md_path = path.with_suffix(".card.md")
            md_path.write_text(self.card.to_markdown())

        return path

    @classmethod
    def load(cls, path: Path) -> "InstitutionalModel":
        with open(path, "rb") as f:
            model = pickle.load(f)
        return model

    def _validate_input(self, X: pd.DataFrame) -> None:
        if not self._fitted:
            raise RuntimeError("Modèle non entraîné — appeler fit() d'abord")
        if self._feature_names:
            missing = [c for c in self._feature_names if c not in X.columns]
            if missing:
                raise ValueError(f"Features manquantes au predict : {missing[:5]}...")
