from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional
import numpy as np

class ReservoirSampler:
    def __init__(self, max_n: int, seed: int = 1337):
        self.max_n = int(max_n)
        self.n_seen = 0
        self.buf: Optional[np.ndarray] = None
        self.rng = np.random.default_rng(seed)

    def add(self, X: np.ndarray):
        X = np.asarray(X, dtype=np.float32)
        if X.ndim != 2:
            raise ValueError("ReservoirSampler expects 2D array [N,F].")
        for i in range(X.shape[0]):
            row = X[i:i+1]
            self.n_seen += 1
            if self.buf is None:
                self.buf = row.copy()
                continue
            if self.buf.shape[0] < self.max_n:
                self.buf = np.vstack([self.buf, row])
            else:
                j = int(self.rng.integers(0, self.n_seen))
                if j < self.max_n:
                    self.buf[j] = row[0]

    def get(self) -> np.ndarray:
        if self.buf is None:
            return np.zeros((0, 0), dtype=np.float32)
        return self.buf

class RobustScaler:
    def __init__(self):
        self.med: Optional[np.ndarray] = None
        self.mad: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray):
        X = np.asarray(X, dtype=np.float32)
        self.med = np.median(X, axis=0)
        mad = np.median(np.abs(X - self.med), axis=0)
        self.mad = np.maximum(mad, 1e-6)

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.med is None or self.mad is None:
            raise RuntimeError("RobustScaler not fitted.")
        X = np.asarray(X, dtype=np.float32)
        return (X - self.med) / (1.4826 * self.mad)

    def to_json(self) -> Dict[str, Any]:
        if self.med is None or self.mad is None:
            raise RuntimeError("RobustScaler not fitted.")
        return {"median": self.med.tolist(), "mad": self.mad.tolist()}

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "RobustScaler":
        sc = RobustScaler()
        sc.med = np.asarray(d["median"], dtype=np.float32)
        sc.mad = np.asarray(d["mad"], dtype=np.float32)
        return sc
