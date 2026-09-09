"""Metrics, computed the way that does not lie.

Two conventions are load-bearing here.

**The t is computed on the gross series.**  If ``net = gross - cost`` with a
constant cost, then ``SE(net) = SE(gross)``, so as the gross tends to zero
``|t_net| -> cost * sqrt(n) / sigma``, which grows without bound in ``n``.  A
mechanism with no edge whatsoever becomes "highly significant" in the direction
of its cost simply by having many episodes.  This is not hypothetical: a
cross-sectional reversal was published here at "-27.5 bps, t = -5.89, the most
significant result of the sweep" whose gross was +0.5 bps and whose gross t was
+0.107.  Four of thirty-one rows of that table were the same artefact.

**The unit of statistics is the episode, the unit of economics is the trade.**
Correlated trades count once for a t-stat, but each of them still pays a cost.
So ``gross_edge_bps`` and ``t_stat`` are episode-level while ``profit_factor``
and the drawdown are trade-level.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from research_kernel.cost_model import breakeven_capture
from research_kernel.declustering import decluster_frame

SECONDS_PER_YEAR = 365.25 * 24 * 3600


class ValidationError(ValueError):
    pass


def _safe(x: float) -> float:
    return float(x) if np.isfinite(x) else 0.0


def _profit_factor(pnl: np.ndarray) -> float:
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    if losses <= 0.0:
        # A profit factor with no losing trade is infinite, which is a sample-size
        # statement, not a performance one.  Report it as such.
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _max_drawdown_bps(pnl: np.ndarray) -> float:
    if len(pnl) == 0:
        return 0.0
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    return float(np.max(peak - equity))


def compute_metrics(
    decisions: pd.DataFrame,
    cost_bps: float,
    declustering_rule: Dict[str, Any],
    timestamp_col: str = "timestamp",
    symbol_col: Optional[str] = "symbol",
    gross_col: str = "gross_bps",
    cross_sectional: bool = False,
) -> Dict[str, Any]:
    """Full metric set for one mechanism run.

    ``decisions`` carries one row per trade with its realised **gross** return in
    basis points, already signed by the side taken.  ``cross_sectional=True``
    declusters across the whole book (one rebalance is one episode) instead of
    per symbol.
    """
    if gross_col not in decisions.columns:
        raise ValidationError("decisions frame has no %r column" % gross_col)
    if len(decisions) == 0:
        raise ValidationError("no decisions to validate")

    window = float(
        declustering_rule.get("window_seconds")
        or declustering_rule.get("same_symbol_gap_seconds")
    )
    method = str(declustering_rule["method"])

    df = decluster_frame(
        decisions,
        window_seconds=window,
        method=method,
        timestamp_col=timestamp_col,
        symbol_col=None if cross_sectional else symbol_col,
    )

    gross = df[gross_col].astype(float).to_numpy()
    net = gross - float(cost_bps)
    net_x2 = gross - 2.0 * float(cost_bps)

    # ---------------------------------------------------------- statistics
    ep = df.groupby("episode_id")[gross_col].mean()
    ep_gross = ep.to_numpy(dtype=float)
    n_independent = int(len(ep_gross))
    n_raw = int(len(df))

    gross_edge_bps = float(np.mean(ep_gross))
    sigma_bps = float(np.std(ep_gross, ddof=1)) if n_independent > 1 else float("nan")
    se_bps = sigma_bps / np.sqrt(n_independent) if n_independent > 1 else float("nan")
    t_stat = _safe(gross_edge_bps / se_bps) if se_bps and np.isfinite(se_bps) else 0.0
    t_stat_net_artefact = (
        _safe((gross_edge_bps - cost_bps) / se_bps)
        if se_bps and np.isfinite(se_bps)
        else 0.0
    )

    # ------------------------------------------------------------ economics
    ts = pd.to_datetime(df[timestamp_col], utc=True)
    span_seconds = float((ts.max() - ts.min()).total_seconds()) or float("nan")
    span_years = span_seconds / SECONDS_PER_YEAR
    span_months = span_years * 12.0

    pf = _profit_factor(net)
    pf_gross = _profit_factor(gross)
    pf_x2 = _profit_factor(net_x2)

    wins = net[net > 0]
    losses = net[net < 0]

    episodes_per_year = n_independent / span_years if span_years and span_years > 0 else float("nan")
    if n_independent > 1 and sigma_bps > 0 and np.isfinite(episodes_per_year):
        ep_net = ep_gross - float(cost_bps)
        sharpe = float(np.mean(ep_net) / np.std(ep_net, ddof=1) * np.sqrt(episodes_per_year))
        downside = ep_net[ep_net < 0]
        dstd = float(np.std(downside, ddof=1)) if len(downside) > 1 else float("nan")
        sortino = (
            float(np.mean(ep_net) / dstd * np.sqrt(episodes_per_year))
            if dstd and np.isfinite(dstd) and dstd > 0
            else float("nan")
        )
    else:
        sharpe = float("nan")
        sortino = float("nan")

    metrics: Dict[str, Any] = {
        "n_raw": n_raw,
        "n_independent": n_independent,
        "declustering_method": method,
        "declustering_window_seconds": window,
        "gross_edge_bps": gross_edge_bps,
        "cost_bps": float(cost_bps),
        "net_edge_bps": gross_edge_bps - float(cost_bps),
        "net_edge_bps_cost_x2": gross_edge_bps - 2.0 * float(cost_bps),
        "breakeven_capture": (
            float(breakeven_capture(gross_edge_bps, float(cost_bps)))
            if gross_edge_bps > 0
            else None
        ),
        "breakeven_capture_note": (
            "null when the gross edge is not positive: there is no fraction of a "
            "move to capture"
        ),
        "sigma_bps": sigma_bps,
        "se_bps": se_bps,
        "t_stat": t_stat,
        "t_stat_on": "gross",
        "t_stat_net_artefact": t_stat_net_artefact,
        "t_stat_net_artefact_note": (
            "diagnostic only — a t on a net series with constant cost measures the "
            "cost, not the signal"
        ),
        "profit_factor": pf,
        "profit_factor_gross": pf_gross,
        "profit_factor_cost_x2": pf_x2,
        "hit_rate": float(np.mean(net > 0)),
        "avg_win_bps": float(np.mean(wins)) if len(wins) else 0.0,
        "avg_loss_bps": float(np.mean(losses)) if len(losses) else 0.0,
        "max_drawdown_bps": _max_drawdown_bps(net),
        "sharpe": sharpe,
        "sortino": sortino,
        "return_total_bps": float(np.sum(net)),
        "return_monthly_bps": float(np.sum(net) / span_months) if span_months else float("nan"),
        "return_note": (
            "bps summed over trades at one unit of notional each, no compounding "
            "and no netting of overlapping positions"
        ),
        "span_start": str(ts.min()),
        "span_end": str(ts.max()),
        "span_years": span_years,
        "episodes_per_year": episodes_per_year,
        "n_symbols": int(df[symbol_col].nunique()) if symbol_col in df.columns else 1,
    }

    metrics["by_year"] = _breakdown(df, net, ts.dt.year.astype(str), gross_col)
    if symbol_col in df.columns:
        metrics["by_symbol"] = _breakdown(df, net, df[symbol_col].astype(str), gross_col)
    else:
        metrics["by_symbol"] = {}

    return metrics


def _breakdown(
    df: pd.DataFrame, net: np.ndarray, key: pd.Series, gross_col: str
) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    tmp = pd.DataFrame({"key": key.to_numpy(), "net": net, "gross": df[gross_col].to_numpy()})
    for k, grp in tmp.groupby("key"):
        out[str(k)] = {
            "n": int(len(grp)),
            "net_bps_mean": float(grp["net"].mean()),
            "net_bps_sum": float(grp["net"].sum()),
            "gross_bps_mean": float(grp["gross"].mean()),
        }
    return out


def leave_one_out_stability(breakdown: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Is the result carried by one year, or one symbol?

    Drops each key in turn and reports the worst remaining mean.  A mechanism
    whose edge disappears when a single year or a single symbol is removed is
    not a mechanism; it is that year or that symbol.
    """
    if len(breakdown) < 2:
        return {"applicable": False, "worst_leave_one_out_net_bps": float("nan"), "worst_key": None}
    total_n = sum(v["n"] for v in breakdown.values())
    total_sum = sum(v["net_bps_sum"] for v in breakdown.values())
    worst = None
    worst_key = None
    for k, v in breakdown.items():
        n = total_n - v["n"]
        if n <= 0:
            continue
        m = (total_sum - v["net_bps_sum"]) / n
        if worst is None or m < worst:
            worst, worst_key = m, k
    return {
        "applicable": True,
        "worst_leave_one_out_net_bps": float(worst) if worst is not None else float("nan"),
        "worst_key": worst_key,
    }


def robustness_gate(
    metrics: Dict[str, Any],
    min_profit_factor: float = 1.20,
    min_independent: int = 200,
    max_drawdown_bps: Optional[float] = None,
) -> List[str]:
    """Gate 3.  Returns the list of failures; empty means it passed."""
    failures: List[str] = []
    if metrics["net_edge_bps"] <= 0:
        failures.append("net_edge_bps %.2f <= 0" % metrics["net_edge_bps"])
    pf = metrics["profit_factor"]
    if not np.isfinite(pf):
        failures.append(
            "profit factor is infinite: no losing trade in %d, which is a sample-size "
            "statement" % metrics["n_raw"]
        )
    elif pf <= min_profit_factor:
        failures.append("profit_factor %.3f <= %.2f" % (pf, min_profit_factor))
    if metrics["n_independent"] < min_independent:
        failures.append(
            "n_independent %d < %d" % (metrics["n_independent"], min_independent)
        )
    if max_drawdown_bps is not None and metrics["max_drawdown_bps"] > max_drawdown_bps:
        failures.append(
            "max_drawdown %.0f bps > %.0f bps" % (metrics["max_drawdown_bps"], max_drawdown_bps)
        )

    year = leave_one_out_stability(metrics.get("by_year", {}))
    metrics["leave_one_year_out"] = year
    if year["applicable"] and year["worst_leave_one_out_net_bps"] <= 0:
        failures.append(
            "edge disappears without %s: %.2f bps"
            % (year["worst_key"], year["worst_leave_one_out_net_bps"])
        )

    sym = leave_one_out_stability(metrics.get("by_symbol", {}))
    metrics["leave_one_symbol_out"] = sym
    if sym["applicable"] and sym["worst_leave_one_out_net_bps"] <= 0:
        failures.append(
            "edge disappears without %s: %.2f bps"
            % (sym["worst_key"], sym["worst_leave_one_out_net_bps"])
        )

    return failures


def min_detectable_edge_bps(se_bps: float, threshold_t: float) -> float:
    """What this sample could see at the family threshold.

    Compare it to the target edge **before** collecting: if the smallest visible
    edge is larger than the edge you hope for, the run is decided in advance.
    """
    if not np.isfinite(se_bps):
        return float("nan")
    return float(threshold_t * se_bps)
