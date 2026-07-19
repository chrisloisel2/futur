"""
src/alpha20/validation/promotion_gate.py — gates uniques + échelle de promotion.

Implémente réellement (pas en placeholder) :
  • DSR — Deflated Sharpe Ratio (Bailey & López de Prado 2014) : probabilité
    que le SR observé batte le SR max attendu sous N essais, corrigé du
    skew/kurtosis. Gate : DSR ≥ 0,95.
  • PBO — Probability of Backtest Overfitting par CSCV (Bailey et al. 2013) :
    fraction des splits combinatoires où la meilleure stratégie IS finit sous
    la médiane OOS. Gate : PBO ≤ 0,10.
  • gate_sleeve : PF ≥ 1,30, coûts ×2 positifs, top-10 événements retirés
    positif, année récente non destructrice (étape 6).
  • ladder : RESEARCH → … → 5 % max (étape 11), exigences par montée.

Chaque gate est PRÉ-ENREGISTRÉ dans configs/alpha20.yaml — aucune valeur ici.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.alpha20 import load_config
from src.alpha20.contracts import GateResult

EULER_GAMMA = 0.5772156649015329


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """Inverse de la CDF normale (approximation d'Acklam, |ε|<1.15e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p dans (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def deflated_sharpe_ratio(returns: pd.Series, n_trials: int) -> float:
    """DSR ∈ [0,1] : P(SR vrai > 0 | N essais). returns = par période."""
    r = pd.Series(returns).dropna()
    T = len(r)
    if T < 30 or r.std(ddof=1) == 0:
        return 0.0
    sr = float(r.mean() / r.std(ddof=1))
    g3 = float(r.skew())
    g4 = float(r.kurtosis()) + 3.0          # pandas = excès → brut
    n = max(int(n_trials), 1)
    if n > 1:
        sr0 = math.sqrt(1.0 / (T - 1)) * (
            (1 - EULER_GAMMA) * _phi_inv(1 - 1.0 / n)
            + EULER_GAMMA * _phi_inv(1 - 1.0 / (n * math.e)))
    else:
        sr0 = 0.0
    denom = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4.0 * sr ** 2, 1e-12))
    return _phi((sr - sr0) * math.sqrt(T - 1) / denom)


def pbo_cscv(returns_matrix: pd.DataFrame, n_blocks: int = 16,
             n_splits: int = 200, seed: int = 42) -> float:
    """PBO par CSCV : returns_matrix (T × N stratégies/configurations).
    Splits combinatoires échantillonnés déterministiquement."""
    X = returns_matrix.dropna(how="all").fillna(0.0).values
    T, N = X.shape
    if T < 2 * n_blocks or N < 2:
        return 1.0                          # indécidable = pire cas (honnête)
    blocks = np.array_split(np.arange(T), n_blocks)
    rng = np.random.RandomState(seed)
    half = n_blocks // 2
    lambdas = []
    for _ in range(n_splits):
        pick = rng.choice(n_blocks, half, replace=False)
        is_idx = np.concatenate([blocks[i] for i in pick])
        oos_idx = np.concatenate([blocks[i] for i in range(n_blocks)
                                  if i not in set(pick)])
        def _sr(idx):
            m = X[idx].mean(axis=0)
            s = X[idx].std(axis=0, ddof=1)
            return np.where(s > 0, m / s, -np.inf)
        best = int(np.argmax(_sr(is_idx)))
        oos_sr = _sr(oos_idx)
        rank = float((oos_sr < oos_sr[best]).sum()) / max(N - 1, 1)
        w = min(max(rank, 1e-6), 1 - 1e-6)
        lambdas.append(math.log(w / (1 - w)))
    return float(np.mean([l <= 0 for l in lambdas]))


# ── gates ────────────────────────────────────────────────────────────────────
def gate_sleeve(net_events: pd.Series, net_events_x2: pd.Series,
                recent_year_net: float) -> List[GateResult]:
    """Gate minimal étape 6 sur une série d'événements/trades NETS."""
    g = load_config()["gates"]
    ev = net_events.dropna()
    pos, neg = ev[ev > 0].sum(), abs(ev[ev < 0].sum())
    pf = float(pos / max(neg, 1e-12))
    top10 = ev.sort_values(ascending=False).iloc[10:].sum() if len(ev) > 10 else -1.0
    return [
        GateResult("pf_min", pf >= g["sleeve_minimal"]["pf_min"], pf,
                   g["sleeve_minimal"]["pf_min"]),
        GateResult("costs_x2_positive", float(net_events_x2.sum()) > 0,
                   float(net_events_x2.sum()), 0.0),
        GateResult("top10_events_removed_positive", float(top10) > 0,
                   float(top10), 0.0),
        GateResult("no_destructive_recent_year", recent_year_net > -0.01,
                   recent_year_net, -0.01,
                   note="2026 ne doit pas détruire le sleeve"),
    ]


def gate_research(returns: pd.Series, n_trials: int,
                  trials_matrix: Optional[pd.DataFrame] = None,
                  corr_with_kept: float = 0.0,
                  capacity_eur: float = 0.0) -> List[GateResult]:
    """Gates uniques étape 10 (les volets nécessitant un re-run — délai,
    univers PIT, moitiés temporelles — sont évalués par le protocole du test,
    pas ici)."""
    g = load_config()["gates"]
    dsr = deflated_sharpe_ratio(returns, n_trials)
    out = [GateResult("dsr_min", dsr >= g["dsr_min"], dsr, g["dsr_min"]),
           GateResult("max_corr_with_kept_sleeves",
                      abs(corr_with_kept) <= g["max_corr_with_kept_sleeves"],
                      corr_with_kept, g["max_corr_with_kept_sleeves"]),
           GateResult("min_capacity", capacity_eur >= g["min_capacity_eur"],
                      capacity_eur, g["min_capacity_eur"])]
    if trials_matrix is not None:
        pbo = pbo_cscv(trials_matrix)
        out.append(GateResult("pbo_max", pbo <= g["pbo_max"], pbo, g["pbo_max"]))
    return out


# ── échelle de promotion ─────────────────────────────────────────────────────
def next_stage(current: str) -> Optional[Dict]:
    ladder = load_config()["promotion"]["ladder"]
    names = [s["stage"] for s in ladder]
    i = names.index(current)
    return ladder[i + 1] if i + 1 < len(ladder) else None


def can_promote(current: str, closed_trades: int, full_cycles: int,
                tracking_error: float, risk_violations: int,
                net_return: float, days_in_stage: int) -> Dict:
    req = load_config()["promotion"]["per_step_requirements"]
    nxt = next_stage(current)
    if nxt is None:
        return {"promote": False, "reason": "déjà au plafond 5 %"}
    stage_def = [s for s in load_config()["promotion"]["ladder"]
                 if s["stage"] == current][0]
    checks = {
        "trades_ou_cycles": (closed_trades >= req["min_closed_trades"]
                             or full_cycles >= req["min_full_cycles_slow"]),
        "tracking_error": tracking_error < req["tracking_error_max"],
        "risk_violations": risk_violations <= req["risk_violations_max"],
        "net_positif": net_return > 0,
        "duree_minimale": days_in_stage >= stage_def.get("min_days", 0),
    }
    return {"promote": all(checks.values()), "next": nxt, "checks": checks}
