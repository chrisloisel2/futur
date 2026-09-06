from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from .hashing import sha256_obj
from .splits import PurgedWalkForwardSplitter
from .statistics import spearman


class ModelAdapter:
    name = "base"

    def fit(self, x: np.ndarray, y: np.ndarray, params: Mapping[str, object]) -> object:
        raise NotImplementedError

    def predict(self, model: object, x: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class RidgeAdapter(ModelAdapter):
    name = "ridge"

    def fit(self, x: np.ndarray, y: np.ndarray, params: Mapping[str, object]) -> object:
        alpha = float(params.get("alpha", 1.0))
        if alpha < 0:
            raise ValueError("alpha must be >=0")
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        mu = np.nanmean(x, axis=0)
        sd = np.nanstd(x, axis=0)
        sd = np.where(sd > 1e-12, sd, 1.0)
        filled = x.copy()
        nan_rows, nan_cols = np.where(~np.isfinite(filled))
        if len(nan_rows):
            filled[nan_rows, nan_cols] = mu[nan_cols]
        xs = (filled - mu) / sd
        ym = float(np.nanmean(y))
        yc = np.nan_to_num(y, nan=ym) - ym
        xtx = xs.T.dot(xs)
        beta = np.linalg.solve(xtx + np.eye(xtx.shape[0]) * alpha, xs.T.dot(yc))
        return {"mu": mu, "sd": sd, "ym": ym, "beta": beta}

    def predict(self, model: object, x: np.ndarray) -> np.ndarray:
        m = model
        raw = np.asarray(x, dtype=float).copy()
        nan_rows, nan_cols = np.where(~np.isfinite(raw))
        if len(nan_rows):
            raw[nan_rows, nan_cols] = m["mu"][nan_cols]
        xs = (raw - m["mu"]) / m["sd"]
        return m["ym"] + xs.dot(m["beta"])


@dataclass(frozen=True)
class NestedFoldResult:
    fold_id: str
    selected_config_digest: str
    selected_params: Mapping[str, object]
    inner_score: float
    outer_ic: float
    n_test: int


@dataclass(frozen=True)
class NestedCVResult:
    predictions: np.ndarray
    folds: tuple[NestedFoldResult, ...]
    tried_config_digests: tuple[str, ...]
    outer_ic: float
    # [n_samples, n_configs], NaN outside each outer fold's own test window. Column j is
    # what every candidate config -- not just the one inner-CV selected -- would have
    # predicted on each fold's held-out set. This is what DSR/PBO need: a real per-trial
    # return series, not just the winning trial's. See build_statistical_evidence().
    predictions_by_config: np.ndarray


def nested_purged_walk_forward(x: np.ndarray, y: np.ndarray, timestamps_ns: Sequence[int], adapter: ModelAdapter, configs: Sequence[Mapping[str, object]], outer_splits: int = 5, inner_splits: int = 3, purge_ms: int = 0, embargo_ms: int = 0) -> NestedCVResult:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    if x.ndim != 2 or len(x) != len(y) or len(y) != len(ts):
        raise ValueError("X/y/timestamps shape mismatch")
    if not configs:
        raise ValueError("at least one model config is required")
    outer = PurgedWalkForwardSplitter(outer_splits, purge_ms=purge_ms, embargo_ms=embargo_ms)
    predictions = np.full(len(y), np.nan, dtype=float)
    predictions_by_config = np.full((len(y), len(configs)), np.nan, dtype=float)
    fold_rows: list[NestedFoldResult] = []
    tried = tuple(sha256_obj(dict(c)) for c in configs)
    for fold in outer.split(ts):
        train_idx = fold.train_idx
        inner_ts = ts[train_idx]
        inner = PurgedWalkForwardSplitter(inner_splits, purge_ms=purge_ms, embargo_ms=embargo_ms, min_train_fraction=0.40)
        scores = []
        for params in configs:
            inner_scores = []
            for inner_fold in inner.split(inner_ts):
                tr = train_idx[inner_fold.train_idx]
                va = train_idx[inner_fold.test_idx]
                model = adapter.fit(x[tr], y[tr], params)
                score = spearman(adapter.predict(model, x[va]), y[va])
                if np.isfinite(score):
                    inner_scores.append(score)
            scores.append(float(np.mean(inner_scores)) if inner_scores else float("-inf"))
        best_i = int(np.argmax(scores))
        params = dict(configs[best_i])
        model = adapter.fit(x[train_idx], y[train_idx], params)
        pred = adapter.predict(model, x[fold.test_idx])
        predictions[fold.test_idx] = pred
        fold_rows.append(NestedFoldResult(fold.fold_id, sha256_obj(params), params, float(scores[best_i]), float(spearman(pred, y[fold.test_idx])), len(fold.test_idx)))
        for j, trial_params in enumerate(configs):
            trial_model = model if j == best_i else adapter.fit(x[train_idx], y[train_idx], trial_params)
            predictions_by_config[fold.test_idx, j] = adapter.predict(trial_model, x[fold.test_idx])
    valid = np.isfinite(predictions) & np.isfinite(y)
    overall = spearman(predictions[valid], y[valid]) if int(valid.sum()) >= 3 else float("nan")
    return NestedCVResult(predictions, tuple(fold_rows), tried, float(overall), predictions_by_config)
