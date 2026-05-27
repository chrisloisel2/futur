from __future__ import annotations

import pickle
from typing import Optional

import numpy as np
import pandas as pd

from pipeline.models.base import BaseModel


class RegimeClassifierModel(BaseModel):
    """
    Simple multinomial logistic regression regime classifier.
    Falls back to empirical priors if sklearn n'est pas disponible.
    """

    def __init__(self, classes: list[str], feature_cols: Optional[list[str]] = None):
        self.classes = classes
        self.feature_cols = feature_cols
        self._sk_model = None
        self._class_priors = np.ones(len(classes)) / len(classes)
        self._mean = None
        self._std = None

    def fit(self, state_df: pd.DataFrame, labels: pd.Series) -> None:
        if state_df.empty or labels.empty:
            raise ValueError("Empty data passed to RegimeClassifierModel.fit")
        X = state_df.select_dtypes(include="number")
        self.feature_cols = list(X.columns)
        self._mean = X.mean()
        self._std = X.std().replace(0, 1.0)
        Xn = (X - self._mean) / self._std
        y = labels.reset_index(drop=True)
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError as exc:  # pragma: no cover - dependency missing
            # fallback to priors only
            counts = y.value_counts()
            self._class_priors = counts.reindex(self.classes).fillna(0).values + 1e-6
            self._class_priors = self._class_priors / self._class_priors.sum()
            self._sk_model = None
            return
        # PRODUCTION FIX: class_weight='balanced' pour corriger class collapse (impulse recall 19.5%)
        model = LogisticRegression(
            max_iter=500,
            class_weight="balanced",  # Critical fix pour impulse recall
            C=2.0,                     # Régularisation plus faible
            n_jobs=-1,
        )
        model.fit(Xn, y)
        self._sk_model = model

    def predict(self, state_df: pd.DataFrame) -> pd.DataFrame:
        if state_df.empty:
            return pd.DataFrame()
        if self.feature_cols is None:
            X = state_df.select_dtypes(include="number")
        else:
            X = state_df[self.feature_cols].copy()
        if self._mean is not None and self._std is not None:
            X = (X - self._mean) / self._std
        if self._sk_model is None:
            probs = np.tile(self._class_priors, (len(X), 1))
        else:
            probs = self._sk_model.predict_proba(X)
        out = pd.DataFrame(probs, columns=self.classes, index=state_df.index)
        out["entropy"] = -(probs * np.log(probs + 1e-9)).sum(axis=1)
        return out

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(self, f)

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            obj = pickle.load(f)
        self.__dict__.update(obj.__dict__)
