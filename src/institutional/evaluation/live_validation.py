"""
src/institutional/evaluation/live_validation.py
─────────────────────────────────────────────────────────────────────────────
Validation live bayésienne + échelle de promotion progressive (cf. brief Étape 13).

On remplace la gate binaire fixe (90j/100 trades/PF>1.30) par :
    P(PF_live > 1.30 | données) ≥ 80%  → micro-live
    P(PF_live > 1.30 | données) ≥ 95%  → live normal
    P(DD_live < 3% | données)    ≥ 95%
    drift = 0

ESS (effective sample size) : 100 trades très corrélés ≠ 100 indépendants.

Échelle : DISABLED → SHADOW → PAPER → MICRO_LIVE → HALF_LIVE → FULL_LIVE.
On ne passe JAMAIS de paper directement à full live.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

LADDER = ["DISABLED", "SHADOW", "PAPER", "MICRO_LIVE", "HALF_LIVE", "FULL_LIVE"]


def profit_factor(returns: np.ndarray) -> float:
    pos = returns[returns > 0].sum()
    neg = -returns[returns < 0].sum()
    if neg <= 1e-12:
        return float("inf") if pos > 0 else 0.0
    return float(pos / neg)


def effective_sample_size(returns: np.ndarray) -> float:
    """ESS via autocorrélation lag-1 : n·(1−ρ)/(1+ρ), bornée [1, n]."""
    n = len(returns)
    if n < 3:
        return float(n)
    r = returns - returns.mean()
    denom = (r ** 2).sum()
    if denom <= 1e-12:
        return float(n)
    rho = float((r[:-1] * r[1:]).sum() / denom)
    rho = max(-0.99, min(0.99, rho))
    return float(np.clip(n * (1 - rho) / (1 + rho), 1.0, n))


def bootstrap_prob_pf_gt(returns: np.ndarray, threshold: float = 1.30,
                         n_boot: int = 2000, seed: int = 0) -> float:
    """P(PF > threshold) par bootstrap des trades."""
    n = len(returns)
    if n < 5:
        return 0.0
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_boot):
        sample = returns[rng.integers(0, n, n)]
        if profit_factor(sample) > threshold:
            hits += 1
    return hits / n_boot


def bootstrap_prob_dd_lt(returns: np.ndarray, max_dd: float = 0.03,
                         n_boot: int = 1000, seed: int = 1) -> float:
    """P(max drawdown de l'equity < max_dd) par bootstrap d'ordonnancements."""
    n = len(returns)
    if n < 5:
        return 0.0
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_boot):
        seq = returns[rng.permutation(n)]
        eq = np.cumprod(1 + seq)
        peak = np.maximum.accumulate(eq)
        dd = ((eq - peak) / peak).min()
        if abs(dd) < max_dd:
            hits += 1
    return hits / n_boot


@dataclass
class LiveValidationResult:
    engine_id: str
    n_trades: int
    ess: float
    pf: float
    p_pf_gt_130: float
    p_dd_lt_3: float
    drift: float
    current_status: str
    recommended_status: str
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine_id": self.engine_id, "n_trades": self.n_trades,
            "ess": round(self.ess, 1), "pf": round(self.pf, 3),
            "p_pf_gt_130": round(self.p_pf_gt_130, 3), "p_dd_lt_3": round(self.p_dd_lt_3, 3),
            "drift": self.drift, "current_status": self.current_status,
            "recommended_status": self.recommended_status, "reasons": self.reasons,
        }


def evaluate_engine(
    engine_id: str,
    trade_returns: np.ndarray,
    current_status: str = "SHADOW",
    drift: float = 0.0,
    shadow_pf: Optional[float] = None,
) -> LiveValidationResult:
    """Recommande le prochain statut sur l'échelle selon les preuves bayésiennes."""
    trade_returns = np.asarray(trade_returns, dtype=float)
    trade_returns = trade_returns[np.isfinite(trade_returns)]
    n = len(trade_returns)
    ess = effective_sample_size(trade_returns) if n else 0.0
    pf = profit_factor(trade_returns) if n else 0.0
    p_pf = bootstrap_prob_pf_gt(trade_returns, 1.30) if n >= 5 else 0.0
    p_dd = bootstrap_prob_dd_lt(trade_returns, 0.03) if n >= 5 else 0.0

    reasons: List[str] = []
    rec = current_status

    # règle de progression (une marche à la fois, jamais paper→full)
    cur_idx = LADDER.index(current_status) if current_status in LADDER else 0

    def _can(level: str) -> int:
        return LADDER.index(level)

    if drift >= 1.0:
        rec = "DISABLED"; reasons.append("drift sévère → DISABLED")
    elif cur_idx <= _can("SHADOW") and (shadow_pf or 0) > 1.10 and n >= 30:
        rec = "PAPER"; reasons.append("shadow sain (PF>1.10, n≥30) → PAPER")
    elif cur_idx == _can("PAPER") and (n >= 50 or ess >= 30) and pf > 1.30 and p_pf >= 0.80 and p_dd >= 0.80:
        rec = "MICRO_LIVE"; reasons.append("P(PF>1.30)≥80% & DD ok → MICRO_LIVE")
    elif cur_idx == _can("MICRO_LIVE") and (n >= 100 or ess >= 60) and p_pf >= 0.95 and p_dd >= 0.95 and drift == 0:
        rec = "HALF_LIVE"; reasons.append("P(PF>1.30)≥95% & P(DD<3%)≥95% → HALF_LIVE")
    elif cur_idx == _can("HALF_LIVE") and (n >= 150 or ess >= 90) and p_pf >= 0.95 and p_dd >= 0.95 and drift == 0:
        rec = "FULL_LIVE"; reasons.append("preuve complète → FULL_LIVE")
    else:
        reasons.append("preuves insuffisantes pour promouvoir — statu quo")

    return LiveValidationResult(
        engine_id=engine_id, n_trades=n, ess=ess, pf=pf, p_pf_gt_130=p_pf,
        p_dd_lt_3=p_dd, drift=drift, current_status=current_status,
        recommended_status=rec, reasons=reasons,
    )
