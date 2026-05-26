"""
research/drift_detector.py — Feature Drift Detection (PSI)

Population Stability Index (PSI):
  PSI < 0.10  → no drift
  PSI 0.10-0.25 → moderate drift
  PSI > 0.25  → significant drift

Usage:
  detector = DriftDetector()
  detector.fit(df_train, feature_cols=["rv_24", "funding_rate_z_72", ...])
  report = detector.score(df_live)
  if detector.is_drifting():
      print(report.top_drifters)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class DriftReport:
    psi_scores: dict[str, float]
    is_drifting: bool
    threshold: float
    top_drifters: list[tuple[str, float]]   # (feature, psi) sorted desc
    n_reference_rows: int
    n_current_rows: int

    def summary(self) -> dict:
        return {
            "is_drifting":    self.is_drifting,
            "max_psi":        round(max(self.psi_scores.values(), default=0), 4),
            "mean_psi":       round(np.mean(list(self.psi_scores.values())), 4),
            "top_drifters":   self.top_drifters[:5],
            "n_drifting_features": sum(
                1 for v in self.psi_scores.values() if v > self.threshold
            ),
        }


class DriftDetector:
    def __init__(self, n_bins: int = 10, psi_threshold: float = 0.25):
        self._n_bins    = n_bins
        self._threshold = psi_threshold
        self._reference: dict[str, np.ndarray] = {}
        self._ref_freq:  dict[str, np.ndarray] = {}
        self._ref_rows:  int = 0
        self._features:  list[str] = []
        self._last_report: Optional[DriftReport] = None
        self._fitted:    bool = False

    # ------------------------------------------------------------------
    # Fit (reference distribution)
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame, feature_cols: Optional[list[str]] = None) -> None:
        cols = feature_cols or [c for c in df.columns if df[c].dtype in (float, "float64", "float32")]
        self._features = cols
        self._ref_rows = len(df)
        self._fitted   = True

        for col in cols:
            series = df[col].dropna().values.astype(float)
            if len(series) < self._n_bins:
                continue
            _, edges = np.histogram(series, bins=self._n_bins)
            freq, _ = np.histogram(series, bins=edges)
            self._reference[col] = edges
            self._ref_freq[col]  = np.maximum(freq / len(series), 1e-6)

    # ------------------------------------------------------------------
    # Score (current distribution)
    # ------------------------------------------------------------------

    def score(self, df: pd.DataFrame) -> DriftReport:
        psi_scores = {}
        for col in self._features:
            if col not in self._reference:
                continue
            series = df[col].dropna().values.astype(float)
            if len(series) < 5:
                continue
            edges = self._reference[col]
            curr_freq, _ = np.histogram(series, bins=edges)
            curr_freq = np.maximum(curr_freq / len(series), 1e-6)
            ref_freq  = self._ref_freq[col]
            psi = float(np.sum((curr_freq - ref_freq) * np.log(curr_freq / ref_freq)))
            psi_scores[col] = round(abs(psi), 6)

        top_drifters = sorted(psi_scores.items(), key=lambda x: x[1], reverse=True)
        drifting = any(v > self._threshold for v in psi_scores.values())

        report = DriftReport(
            psi_scores      = psi_scores,
            is_drifting     = drifting,
            threshold       = self._threshold,
            top_drifters    = top_drifters,
            n_reference_rows= self._ref_rows,
            n_current_rows  = len(df),
        )
        self._last_report = report
        return report

    def is_drifting(self) -> bool:
        if self._last_report is None:
            return False
        return self._last_report.is_drifting

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        np.save(path / "ref_freq.npy",   self._pack_arrays(self._ref_freq))
        np.save(path / "reference.npy",  self._pack_arrays(self._reference))
        (path / "meta.json").write_text(json.dumps({
            "features":    self._features,
            "n_bins":      self._n_bins,
            "threshold":   self._threshold,
            "ref_rows":    self._ref_rows,
        }))

    def load(self, path: Path) -> None:
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        self._features  = meta["features"]
        self._n_bins    = meta["n_bins"]
        self._threshold = meta["threshold"]
        self._ref_rows  = meta["ref_rows"]
        self._ref_freq  = dict(zip(self._features, np.load(path / "ref_freq.npy", allow_pickle=True)))
        self._reference = dict(zip(self._features, np.load(path / "reference.npy", allow_pickle=True)))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _pack_arrays(self, d: dict) -> np.ndarray:
        return np.array([d.get(f, np.array([])) for f in self._features], dtype=object)
