"""
ai/regime/hmm_engine.py — Gaussian HMM (Baum-Welch + Viterbi), from scratch.

3 états latents:
  0 = BEAR / DELEVERAGING  (vol haute, momentum négatif, funding extrême)
  1 = NEUTRAL / COMPRESSION (vol normale, pas de tendance claire)
  2 = BULL / EXPANSION      (vol modérée-haute, momentum positif, OI croissant)

Features utilisées (disponibles dans les parquets existants):
  rv_24, rv_72, mom_logret_72 (calculé comme log-return 72h),
  funding_rate_z_72, oi_acceleration_z (si absent → 0)

Usage:
  eng = GaussianHMMEngine(n_states=3)
  eng.fit(df, train_mask)
  states = eng.predict(df)                # Viterbi
  probs  = eng.predict_proba(df)          # forward probabilities (n, 3)
  state, conf = eng.current_state(row)
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# Features candidates (use only those present in the DataFrame)
_CANDIDATE_FEATURES = [
    "rv_24", "rv_72", "rv_48",
    "mom_logret_72",
    "funding_rate_z_72", "funding_rate",
    "oi_acceleration_z", "global_ls_longShortRatio_z_72",
    "atr_pct_14",
]
_FALLBACK_FEATURES = ["rv_24", "rv_72", "atr_pct_14"]


def _select_features(df: pd.DataFrame, candidates: list[str]) -> list[str]:
    return [c for c in candidates if c in df.columns]


class GaussianHMMEngine:
    """
    Gaussian Hidden Markov Model implemented from scratch (no external HMM lib).

    Emission: multivariate Gaussian per state (diagonal covariance for stability).
    Transition: full transition matrix, learned via Baum-Welch EM.
    Decoding:  Viterbi (MAP state sequence).
    """

    def __init__(self, n_states: int = 3, n_iter: int = 50, tol: float = 1e-4):
        self.n_states = n_states
        self.n_iter   = n_iter
        self.tol      = tol
        self._features: list[str] = []

        # Model parameters (set by fit)
        self._pi:     Optional[np.ndarray] = None   # (n_states,) initial probs
        self._A:      Optional[np.ndarray] = None   # (n_states, n_states) transition
        self._mu:     Optional[np.ndarray] = None   # (n_states, n_features) means
        self._sigma2: Optional[np.ndarray] = None   # (n_states, n_features) variances
        self._fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(
        self,
        df: pd.DataFrame,
        train_mask: Optional[pd.Series] = None,
        feature_cols: Optional[list[str]] = None,
    ) -> "GaussianHMMEngine":
        if feature_cols:
            self._features = [c for c in feature_cols if c in df.columns]
        else:
            self._features = _select_features(df, _CANDIDATE_FEATURES)
            if len(self._features) < 2:
                self._features = _select_features(df, _FALLBACK_FEATURES)

        subset = df[train_mask] if train_mask is not None else df
        X = self._extract(subset)
        X = self._impute(X)

        self._baum_welch(X)
        self._fitted = True
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X = self._impute(self._extract(df))
        return self._viterbi(X)

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        X = self._impute(self._extract(df))
        alpha, _ = self._forward(X)
        norm = alpha.sum(axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return alpha / norm

    def current_state(self, row: pd.Series) -> tuple[int, float]:
        df_row = pd.DataFrame([row])
        probs = self.predict_proba(df_row)[0]
        state = int(np.argmax(probs))
        return state, float(probs[state])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "n_states":  self.n_states,
            "features":  self._features,
            "pi":        self._pi.tolist(),
            "A":         self._A.tolist(),
            "mu":        self._mu.tolist(),
            "sigma2":    self._sigma2.tolist(),
        }
        path.with_suffix(".json").write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "GaussianHMMEngine":
        data = json.loads(Path(path).with_suffix(".json").read_text())
        eng = cls(n_states=data["n_states"])
        eng._features = data["features"]
        eng._pi       = np.array(data["pi"])
        eng._A        = np.array(data["A"])
        eng._mu       = np.array(data["mu"])
        eng._sigma2   = np.array(data["sigma2"])
        eng._fitted   = True
        return eng

    # ------------------------------------------------------------------
    # Baum-Welch EM
    # ------------------------------------------------------------------

    def _baum_welch(self, X: np.ndarray) -> None:
        T, D = X.shape
        K    = self.n_states

        # Initialize with k-means-like assignment
        self._pi     = np.full(K, 1.0 / K)
        self._A      = np.full((K, K), 1.0 / K)
        self._mu     = self._kmeans_init(X, K)
        self._sigma2 = np.var(X, axis=0, keepdims=True).repeat(K, axis=0) + 1e-6

        log_lik_prev = -np.inf
        for _ in range(self.n_iter):
            # E-step
            log_B      = self._log_emission(X)        # (T, K)
            alpha, c   = self._forward_log(log_B)     # (T, K), (T,)
            beta        = self._backward_log(log_B, c) # (T, K)

            gamma = alpha * beta                       # (T, K)
            gamma /= gamma.sum(axis=1, keepdims=True).clip(1e-300)

            xi = np.zeros((T - 1, K, K))
            for t in range(T - 1):
                for i in range(K):
                    for j in range(K):
                        xi[t, i, j] = (
                            alpha[t, i] * self._A[i, j]
                            * np.exp(log_B[t + 1, j]) * beta[t + 1, j]
                        )
                row_sum = xi[t].sum()
                if row_sum > 0:
                    xi[t] /= row_sum

            # M-step
            self._pi = gamma[0].clip(1e-300)
            self._pi /= self._pi.sum()

            xi_sum = xi.sum(axis=0)                   # (K, K)
            row_sum = xi_sum.sum(axis=1, keepdims=True).clip(1e-300)
            self._A = (xi_sum / row_sum).clip(1e-300)
            self._A /= self._A.sum(axis=1, keepdims=True)

            gamma_sum = gamma.sum(axis=0).clip(1e-300)  # (K,)
            self._mu     = (gamma.T @ X) / gamma_sum[:, None]
            diff         = X[None, :, :] - self._mu[:, None, :]    # (K, T, D)
            self._sigma2 = (gamma.T[:, :, None] * diff ** 2).sum(axis=1) / gamma_sum[:, None]
            self._sigma2 = self._sigma2.clip(1e-6)

            log_lik = c.sum()
            if abs(log_lik - log_lik_prev) < self.tol:
                break
            log_lik_prev = log_lik

    # ------------------------------------------------------------------
    # Forward / Backward (log-scale for numerical stability)
    # ------------------------------------------------------------------

    def _log_emission(self, X: np.ndarray) -> np.ndarray:
        T, D = X.shape
        K    = self.n_states
        log_B = np.zeros((T, K))
        for k in range(K):
            diff = X - self._mu[k]
            log_B[:, k] = -0.5 * (
                D * np.log(2 * np.pi)
                + np.sum(np.log(self._sigma2[k]))
                + np.sum(diff ** 2 / self._sigma2[k], axis=1)
            )
        return log_B

    def _forward_log(self, log_B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        T, K = log_B.shape
        alpha = np.zeros((T, K))
        c     = np.zeros(T)

        alpha[0] = self._pi * np.exp(log_B[0])
        c[0]     = alpha[0].sum()
        alpha[0] /= c[0].clip(1e-300)

        for t in range(1, T):
            alpha[t] = (alpha[t - 1] @ self._A) * np.exp(log_B[t])
            c[t]     = alpha[t].sum()
            alpha[t] /= c[t].clip(1e-300)

        c = np.log(c.clip(1e-300))
        return alpha, c

    def _forward(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        log_B = self._log_emission(X)
        return self._forward_log(log_B)

    def _backward_log(self, log_B: np.ndarray, log_c: np.ndarray) -> np.ndarray:
        T, K = log_B.shape
        beta  = np.zeros((T, K))
        beta[-1] = 1.0

        for t in range(T - 2, -1, -1):
            beta[t] = (self._A * np.exp(log_B[t + 1]) * beta[t + 1]).sum(axis=1)
            beta[t] /= np.exp(log_c[t + 1]).clip(1e-300)

        return beta

    # ------------------------------------------------------------------
    # Viterbi decoding
    # ------------------------------------------------------------------

    def _viterbi(self, X: np.ndarray) -> np.ndarray:
        T, D  = X.shape
        K     = self.n_states
        log_B = self._log_emission(X)
        log_A = np.log(self._A.clip(1e-300))
        log_pi= np.log(self._pi.clip(1e-300))

        delta = np.full((T, K), -np.inf)
        psi   = np.zeros((T, K), dtype=int)

        delta[0] = log_pi + log_B[0]
        for t in range(1, T):
            for j in range(K):
                scores = delta[t - 1] + log_A[:, j]
                psi[t, j]   = int(np.argmax(scores))
                delta[t, j] = scores[psi[t, j]] + log_B[t, j]

        states    = np.zeros(T, dtype=int)
        states[-1] = int(np.argmax(delta[-1]))
        for t in range(T - 2, -1, -1):
            states[t] = psi[t + 1, states[t + 1]]
        return states

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract(self, df: pd.DataFrame) -> np.ndarray:
        cols = [c for c in self._features if c in df.columns]
        return df[cols].values.astype(float)

    def _impute(self, X: np.ndarray) -> np.ndarray:
        col_means = np.nanmean(X, axis=0)
        idx = np.where(np.isnan(X))
        X[idx] = np.take(col_means, idx[1])
        return X

    def _kmeans_init(self, X: np.ndarray, K: int, n_iter: int = 20) -> np.ndarray:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(X), size=K, replace=False)
        centers = X[idx].copy()
        for _ in range(n_iter):
            dists   = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)
            labels  = np.argmin(dists, axis=1)
            new_ctr = np.array([
                X[labels == k].mean(axis=0) if (labels == k).any() else centers[k]
                for k in range(K)
            ])
            if np.allclose(centers, new_ctr, atol=1e-6):
                break
            centers = new_ctr
        return centers
