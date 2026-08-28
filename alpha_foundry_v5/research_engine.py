from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from .contracts import ExperimentSpec, HypothesisSpec, ResearchStage
from .hashing import sha256_obj
from .labs.registry import LabRegistry
from .ledger import SearchLedger
from .modeling import ModelAdapter, RidgeAdapter, nested_purged_walk_forward
from .statistics import bh_qvalues, block_permutation_pvalue, effective_sample_size, spearman
from .targets import make_target


@dataclass(frozen=True)
class DiscoveryResult:
    hypothesis_id: str
    hypothesis_digest: str
    experiment_digest: str
    n: int
    ess: float
    ic: float
    block_p: float
    q_value: float
    fold_ics: Tuple[float, ...]
    tried_configs: Tuple[str, ...]
    prediction: np.ndarray
    target: np.ndarray
    timestamps_ns: np.ndarray


class ResearchEngine:
    """Nested OOS research while charging every selectable configuration to a search ledger."""

    def __init__(self, ledger: SearchLedger, lab_registry: LabRegistry = None):
        self.ledger = ledger
        self.labs = lab_registry or LabRegistry()

    def _target(self, frame: pd.DataFrame, hypothesis: HypothesisSpec, cadence_ms: int) -> pd.Series:
        steps = max(1, int(round(float(hypothesis.horizon_ms) / float(cadence_ms))))
        kwargs = {}
        if hypothesis.target_name == "loo_fair_value_return":
            excluded = hypothesis.feature_set_id.split("venue=", 1)[1].split(",", 1)[0] if "venue=" in hypothesis.feature_set_id else "okx"
            kwargs = {"excluded_venue": excluded, "venues": ("binance", "bybit", "okx", "hyperliquid")}
        if "symbol" not in frame.columns:
            return make_target(frame, hypothesis.target_name, steps, **kwargs)
        target = pd.Series(index=frame.index, dtype=float)
        for _symbol, group in frame.groupby("symbol", sort=False):
            ordered = group.sort_values("asof_ns", kind="mergesort") if "asof_ns" in group else group
            values = make_target(ordered, hypothesis.target_name, steps, **kwargs)
            target.loc[ordered.index] = values.to_numpy()
        return target

    def run_discovery(self, frame: pd.DataFrame, hypothesis: HypothesisSpec, experiment: ExperimentSpec, cadence_ms: int, configs: Sequence[Mapping[str, object]], adapter: ModelAdapter = None, outer_splits: int = 5, inner_splits: int = 3, block_size_rows: int = 3000) -> DiscoveryResult:
        if experiment.stage != ResearchStage.DEV_DISCOVERY:
            raise ValueError("run_discovery requires DEV_DISCOVERY experiment")
        if experiment.hypothesis_digest != hypothesis.digest:
            raise ValueError("experiment/hypothesis digest mismatch")
        if "asof_ns" not in frame:
            raise ValueError("frame requires asof_ns")
        sort_cols = ["asof_ns"] + (["symbol"] if "symbol" in frame.columns else [])
        frame = frame.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
        features = self.labs.materialize_features(hypothesis.lab_id, frame)
        target = self._target(frame, hypothesis, cadence_ms)
        numeric = features.apply(pd.to_numeric, errors="coerce")
        usable_cols = [c for c in numeric.columns if numeric[c].notna().sum() >= max(100, int(0.10 * len(numeric)))]
        if not usable_cols:
            raise ValueError("no usable features for %s" % hypothesis.lab_id)
        x = numeric[usable_cols].to_numpy(dtype=float)
        y = pd.to_numeric(target, errors="coerce").to_numpy(dtype=float)
        ts_series = pd.to_numeric(frame["asof_ns"], errors="coerce")
        valid = np.isfinite(y) & ts_series.notna().to_numpy()
        ts = ts_series.fillna(0).astype(np.int64).to_numpy()
        x = x[valid]
        y = y[valid]
        ts = ts[valid]
        if len(y) < 500:
            raise ValueError("insufficient rows after target alignment")
        adapter = adapter or RidgeAdapter()
        for i, config in enumerate(configs):
            trial_id = "%s-%03d-%s" % (experiment.experiment_id, i, sha256_obj(dict(config))[:10])
            self.ledger.reserve(trial_id, hypothesis.family_id, hypothesis.digest, experiment.digest, dict(config), experiment.stage.value, hypothesis.max_trials)
        purge_ms = max(int(hypothesis.horizon_ms), int(hypothesis.max_lookback_ms), int(experiment.lookback_ms))
        nested = nested_purged_walk_forward(x, y, ts, adapter, configs, outer_splits=outer_splits, inner_splits=inner_splits, purge_ms=purge_ms, embargo_ms=int(hypothesis.horizon_ms))
        pred = nested.predictions
        pv = np.isfinite(pred) & np.isfinite(y)
        ic = float(spearman(pred[pv], y[pv]))
        ess = float(effective_sample_size(pred[pv]))
        block_p = float(block_permutation_pvalue(pred[pv], y[pv], block_size=block_size_rows, repeats=200, seed=experiment.seed))
        fold_ics = tuple(float(f.outer_ic) for f in nested.folds)
        for i, config in enumerate(configs):
            trial_id = "%s-%03d-%s" % (experiment.experiment_id, i, sha256_obj(dict(config))[:10])
            selected_scores = [f.inner_score for f in nested.folds if f.selected_config_digest == sha256_obj(dict(config))]
            metric = float(np.mean(selected_scores)) if selected_scores else float("nan")
            self.ledger.complete(trial_id, hypothesis.family_id, hypothesis.digest, experiment.digest, dict(config), experiment.stage.value, metric)
        return DiscoveryResult(hypothesis.hypothesis_id, hypothesis.digest, experiment.digest, int(pv.sum()), ess, ic, block_p, float("nan"), fold_ics, tuple(nested.tried_config_digests), pred, y, ts)

    def finalize_family(self, results: Sequence[DiscoveryResult]) -> Tuple[DiscoveryResult, ...]:
        if not results:
            return tuple()
        q = bh_qvalues([r.block_p for r in results])
        return tuple(replace(r, q_value=float(q[i])) for i, r in enumerate(results))
