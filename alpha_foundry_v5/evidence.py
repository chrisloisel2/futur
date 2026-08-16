from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple

import numpy as np

from .contracts import EconomicEvidence, StatisticalEvidence, TimeWindow
from .statistics import bh_qvalues, block_permutation_pvalue, cscv_pbo, deflated_sharpe_probability, effective_sample_size, sharpe_ratio, spearman
from .validation import max_drawdown, profit_factor


@dataclass(frozen=True)
class StatisticalInputs:
    signal: Sequence[float]
    target: Sequence[float]
    pvalue_family: Sequence[float]
    own_pvalue_index: int
    net_returns: Sequence[float]
    trial_returns: np.ndarray
    symbol_ics: Mapping[str, float]
    primary_symbols: Tuple[str, ...]
    discovery_window: TimeWindow
    evaluation_window: TimeWindow
    block_size: int
    expected_sign: int = 1


def build_statistical_evidence(inputs: StatisticalInputs) -> StatisticalEvidence:
    signal = np.asarray(inputs.signal, dtype=float)
    target = np.asarray(inputs.target, dtype=float)
    valid = np.isfinite(signal) & np.isfinite(target)
    x = signal[valid]
    y = target[valid]
    n = int(len(x))
    ic = float(spearman(x, y))
    ess = float(effective_sample_size(x))
    q = bh_qvalues(inputs.pvalue_family)
    q_value = float(q[int(inputs.own_pvalue_index)])
    block_p = float(block_permutation_pvalue(x, y, block_size=int(inputs.block_size), repeats=200))
    trial_matrix = np.asarray(inputs.trial_returns, dtype=float)
    if trial_matrix.ndim != 2 or trial_matrix.shape[1] < 2:
        raise ValueError("trial_returns must be [time, trials] with >=2 trials")
    trial_sharpes = [sharpe_ratio(trial_matrix[:, j]) for j in range(trial_matrix.shape[1])]
    dsr = float(deflated_sharpe_probability(inputs.net_returns, trial_sharpes))
    pbo = float(cscv_pbo(trial_matrix, n_blocks=10))
    half = n // 2
    same_sign_halves = False
    if half >= 3:
        a = spearman(x[:half], y[:half])
        b = spearman(x[half:], y[half:])
        same_sign_halves = bool(np.isfinite(a) and np.isfinite(b) and np.sign(a) == np.sign(ic) and np.sign(b) == np.sign(ic))
    if int(inputs.expected_sign) not in {-1, 1}:
        raise ValueError("expected_sign must be -1 or +1")
    primary_pass = all(int(inputs.expected_sign) * float(inputs.symbol_ics.get(s, float("nan"))) > 0 for s in inputs.primary_symbols)
    independent = not inputs.discovery_window.overlaps(inputs.evaluation_window) and int(inputs.evaluation_window.start_ns) > int(inputs.discovery_window.stop_ns)
    return StatisticalEvidence(n, ess, ic, q_value, block_p, dsr, pbo, same_sign_halves, bool(primary_pass), bool(independent), False)


@dataclass(frozen=True)
class EconomicInputs:
    gross_trade_pnl_bps: Sequence[float]
    base_cost_bps: Sequence[float]
    delayed_trade_pnl_bps: Sequence[float]
    capacity_usd: float
    recent_trade_pnl_bps: Sequence[float]
    paper_live_trade_pnl_bps: Sequence[float]
    fill_rate: float
    realized_slippage_bps: float
    remove_top_fraction: float = 0.10


def build_economic_evidence(inputs: EconomicInputs) -> EconomicEvidence:
    gross = np.asarray(inputs.gross_trade_pnl_bps, dtype=float)
    costs = np.asarray(inputs.base_cost_bps, dtype=float)
    delayed = np.asarray(inputs.delayed_trade_pnl_bps, dtype=float)
    if len(gross) != len(costs):
        raise ValueError("gross/cost length mismatch")
    net = gross - costs
    net_x2 = gross - 2.0 * costs
    finite_net = net[np.isfinite(net)]
    k = int(np.floor(len(finite_net) * float(inputs.remove_top_fraction)))
    trimmed = np.sort(finite_net)[:-k] if k > 0 else finite_net
    recent = np.asarray(inputs.recent_trade_pnl_bps, dtype=float)
    paper = np.asarray(inputs.paper_live_trade_pnl_bps, dtype=float)
    return EconomicEvidence(float(np.nanmean(gross)) if len(gross) else float("nan"), float(np.nanmean(net)) if len(net) else float("nan"), float(np.nanmean(net_x2)) if len(net_x2) else float("nan"), float(np.nanmean(delayed)) if len(delayed) else float("nan"), float(profit_factor(net)), float(max_drawdown(net / 1e4)), float(inputs.capacity_usd), float(np.nanmean(trimmed)) if len(trimmed) else float("nan"), float(np.nanmean(recent)) if len(recent) else float("nan"), float(np.nanmean(paper)) if len(paper) else float("nan"), float(inputs.fill_rate), float(inputs.realized_slippage_bps))
