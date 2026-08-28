from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .contracts import (
    ExperimentSpec,
    HypothesisSpec,
    ResearchStage,
    StatisticalEvidence,
    TimeWindow,
)
from .evidence import StatisticalInputs, build_statistical_evidence
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
    fold_ics: tuple[float, ...]
    tried_configs: tuple[str, ...]
    prediction: np.ndarray
    target: np.ndarray
    timestamps_ns: np.ndarray
    symbol_ics: Mapping[str, float]
    trial_returns: np.ndarray


@dataclass(frozen=True)
class ConfirmationResult:
    hypothesis_id: str
    hypothesis_digest: str
    experiment_digest: str
    n: int
    ess: float
    ic: float
    block_p: float
    fold_ics: tuple[float, ...]
    tried_configs: tuple[str, ...]
    prediction: np.ndarray
    target: np.ndarray
    timestamps_ns: np.ndarray
    symbol_ics: Mapping[str, float]
    trial_returns: np.ndarray


class ResearchEngine:
    """Nested OOS research while charging every selectable configuration to a search ledger."""

    def __init__(self, ledger: SearchLedger, lab_registry: LabRegistry | None = None):
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

    def _prepare_xyt(self, frame: pd.DataFrame, hypothesis: HypothesisSpec, cadence_ms: int):
        if "asof_ns" not in frame:
            raise ValueError("frame requires asof_ns")
        sort_cols = ["asof_ns"] + (["symbol"] if "symbol" in frame.columns else [])
        frame = frame.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
        features = self.labs.materialize_features(hypothesis.lab_id, frame)
        target = self._target(frame, hypothesis, cadence_ms)
        numeric = features.apply(pd.to_numeric, errors="coerce")
        usable_cols = [c for c in numeric.columns if numeric[c].notna().sum() >= max(100, int(0.10 * len(numeric)))]
        if not usable_cols:
            raise ValueError(f"no usable features for {hypothesis.lab_id}")
        x = numeric[usable_cols].to_numpy(dtype=float)
        y = pd.to_numeric(target, errors="coerce").to_numpy(dtype=float)
        ts_series = pd.to_numeric(frame["asof_ns"], errors="coerce")
        valid = np.isfinite(y) & ts_series.notna().to_numpy()
        ts = ts_series.fillna(0).astype(np.int64).to_numpy()
        symbols = frame["symbol"].astype(str).to_numpy() if "symbol" in frame.columns else np.full(len(frame), "ALL")
        x = x[valid]
        y = y[valid]
        ts = ts[valid]
        symbols = symbols[valid]
        if len(y) < 500:
            raise ValueError("insufficient rows after target alignment")
        return x, y, ts, symbols

    def _symbol_ics(self, signal: np.ndarray, target: np.ndarray, symbols: np.ndarray) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym in sorted(set(symbols.tolist())):
            mask = symbols == sym
            out[sym] = float(spearman(signal[mask], target[mask]))
        return out

    def _run_nested(self, x: np.ndarray, y: np.ndarray, ts: np.ndarray, symbols: np.ndarray, hypothesis: HypothesisSpec, experiment: ExperimentSpec, configs: Sequence[Mapping[str, object]], adapter: ModelAdapter | None, outer_splits: int, inner_splits: int, block_size_rows: int):
        adapter = adapter or RidgeAdapter()
        for i, config in enumerate(configs):
            trial_id = f"{experiment.experiment_id}-{i:03d}-{sha256_obj(dict(config))[:10]}"
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
            trial_id = f"{experiment.experiment_id}-{i:03d}-{sha256_obj(dict(config))[:10]}"
            selected_scores = [f.inner_score for f in nested.folds if f.selected_config_digest == sha256_obj(dict(config))]
            metric = float(np.mean(selected_scores)) if selected_scores else float("nan")
            self.ledger.complete(trial_id, hypothesis.family_id, hypothesis.digest, experiment.digest, dict(config), experiment.stage.value, metric)
        symbol_ics = self._symbol_ics(pred[pv], y[pv], symbols[pv])
        trial_returns = nested.predictions_by_config[pv] * y[pv, None]
        return nested, pv, ic, ess, block_p, fold_ics, symbol_ics, trial_returns

    def run_discovery(self, frame: pd.DataFrame, hypothesis: HypothesisSpec, experiment: ExperimentSpec, cadence_ms: int, configs: Sequence[Mapping[str, object]], adapter: ModelAdapter | None = None, outer_splits: int = 5, inner_splits: int = 3, block_size_rows: int = 3000) -> DiscoveryResult:
        if experiment.stage != ResearchStage.DEV_DISCOVERY:
            raise ValueError("run_discovery requires DEV_DISCOVERY experiment")
        if experiment.hypothesis_digest != hypothesis.digest:
            raise ValueError("experiment/hypothesis digest mismatch")
        x, y, ts, symbols = self._prepare_xyt(frame, hypothesis, cadence_ms)
        nested, pv, ic, ess, block_p, fold_ics, symbol_ics, trial_returns = self._run_nested(x, y, ts, symbols, hypothesis, experiment, configs, adapter, outer_splits, inner_splits, block_size_rows)
        return DiscoveryResult(hypothesis.hypothesis_id, hypothesis.digest, experiment.digest, int(pv.sum()), ess, ic, block_p, float("nan"), fold_ics, tuple(nested.tried_config_digests), nested.predictions, y, ts, symbol_ics, trial_returns)

    def run_confirmation(self, frame: pd.DataFrame, hypothesis: HypothesisSpec, experiment: ExperimentSpec, cadence_ms: int, configs: Sequence[Mapping[str, object]], adapter: ModelAdapter | None = None, outer_splits: int = 5, inner_splits: int = 3, block_size_rows: int = 3000) -> ConfirmationResult:
        """Same nested-CV machinery as run_discovery, on a confirmation-stage experiment.

        Independence from discovery is NOT decided here -- registering `experiment` through
        ExperimentRegistry (stage=INDEPENDENT_CONFIRMATION) already refuses an overlapping or
        non-strictly-after window for this hypothesis_digest before this method is even
        reachable. This method only computes the statistics; build_statistical_evidence()
        (called by the caller once discovery_window is known) computes independent_window.
        """
        if experiment.stage != ResearchStage.INDEPENDENT_CONFIRMATION:
            raise ValueError("run_confirmation requires INDEPENDENT_CONFIRMATION experiment")
        if experiment.hypothesis_digest != hypothesis.digest:
            raise ValueError("experiment/hypothesis digest mismatch")
        x, y, ts, symbols = self._prepare_xyt(frame, hypothesis, cadence_ms)
        nested, pv, ic, ess, block_p, fold_ics, symbol_ics, trial_returns = self._run_nested(x, y, ts, symbols, hypothesis, experiment, configs, adapter, outer_splits, inner_splits, block_size_rows)
        return ConfirmationResult(hypothesis.hypothesis_id, hypothesis.digest, experiment.digest, int(pv.sum()), ess, ic, block_p, fold_ics, tuple(nested.tried_config_digests), nested.predictions, y, ts, symbol_ics, trial_returns)

    def finalize_family(self, results: Sequence[DiscoveryResult]) -> tuple[DiscoveryResult, ...]:
        if not results:
            return tuple()
        q = bh_qvalues([r.block_p for r in results])
        return tuple(replace(r, q_value=float(q[i])) for i, r in enumerate(results))


def build_evidence(
    result: DiscoveryResult | ConfirmationResult,
    *,
    stage: ResearchStage,
    pvalue_family: Sequence[float],
    own_pvalue_index: int,
    primary_symbols: Sequence[str],
    discovery_window: TimeWindow,
    evaluation_window: TimeWindow,
    block_size_rows: int = 3000,
    expected_sign: int = 1,
) -> StatisticalEvidence:
    """Build the StatisticalEvidence a stage's gate actually checks, from a Discovery/
    ConfirmationResult's raw components plus the family-wide p-value context only the
    caller (which owns the multiplicity ledger) has. DEV_DISCOVERY callers should pass
    discovery_window == evaluation_window == experiment.window (independent_window is not
    checked at that stage, but the object must still be constructible).
    """
    signal = result.prediction
    target = result.target
    net_returns = signal[np.isfinite(signal) & np.isfinite(target)] * target[np.isfinite(signal) & np.isfinite(target)]
    inputs = StatisticalInputs(
        signal=signal,
        target=target,
        pvalue_family=pvalue_family,
        own_pvalue_index=own_pvalue_index,
        net_returns=net_returns,
        trial_returns=result.trial_returns,
        symbol_ics=result.symbol_ics,
        primary_symbols=tuple(primary_symbols),
        discovery_window=discovery_window,
        evaluation_window=evaluation_window,
        block_size=block_size_rows,
        expected_sign=expected_sign,
    )
    return build_statistical_evidence(inputs)
