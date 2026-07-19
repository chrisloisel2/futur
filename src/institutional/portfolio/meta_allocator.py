"""
src/institutional/portfolio/meta_allocator.py
─────────────────────────────────────────────────────────────────────────────
Méta-Allocateur (utility scoring) — cf. brief Étape 11.

Il NE prédit pas le marché. Il CHOISIT le capital entre des Opportunity venant
de moteurs indépendants :

    utility = expected_return
              − cost
              − γ · expected_risk
              − β · correlation_penalty
              − η · drawdown_penalty
              − κ · turnover_penalty

Prend les opportunités par utility décroissante en respectant les contraintes
(constraints.py) et le sizing multi-cap (sizing.py). S'utilise comme
`allocator_hook` du PortfolioBacktester.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.institutional.portfolio.constraints import PortfolioConstraints
from src.institutional.portfolio.correlation_model import CorrelationModel
from src.institutional.portfolio.sizing import SizingCaps, multi_cap_size

logger = logging.getLogger(__name__)


@dataclass
class UtilityWeights:
    # Calibrés pour être commensurables avec expected_return par trade (~0.3-1%).
    gamma: float = 0.10   # pénalité risque (vol sur l'horizon)
    beta: float = 0.10    # pénalité corrélation aux positions ouvertes
    eta: float = 0.10     # pénalité drawdown courant
    kappa: float = 0.0005 # pénalité turnover (par nouveau trade)


class UtilityMetaAllocator:
    def __init__(
        self,
        constraints: Optional[PortfolioConstraints] = None,
        caps: Optional[SizingCaps] = None,
        weights: Optional[UtilityWeights] = None,
        correlation: Optional[CorrelationModel] = None,
        target_vol: float = 0.15,
        live_mode: bool = False,
    ):
        self.constraints = constraints or PortfolioConstraints()
        self.caps = caps or SizingCaps()
        self.w = weights or UtilityWeights()
        self.corr = correlation or CorrelationModel()
        self.target_vol = target_vol
        self.live_mode = live_mode  # False = backtest (taille normale)

    def _risk_over_horizon(self, expected_vol: float, holding_hours: float) -> float:
        return float(expected_vol) * math.sqrt(max(holding_hours, 1.0) / 8760.0)

    def utility(self, row, open_assets: list, portfolio_dd: float) -> float:
        risk = self._risk_over_horizon(row["expected_vol"], row["holding_hours"])
        corr_pen = max((self.corr.correlation(row["asset"], a) for a in open_assets), default=0.0)
        dd_pen = abs(min(0.0, portfolio_dd))
        return float(
            row["expected_return"]
            - row["expected_cost"]
            - self.w.gamma * risk
            - self.w.beta * corr_pen
            - self.w.eta * dd_pen
            - self.w.kappa
        )

    def allocate(self, cands: pd.DataFrame, ctx: Dict) -> List[Tuple[pd.Series, float]]:
        """Retourne [(row, fraction)] respectant contraintes + sizing."""
        open_positions = ctx.get("open_positions", [])
        equity = ctx.get("equity", 1.0)
        gov_mult = ctx.get("gov_mult", 1.0)
        portfolio_dd = ctx.get("portfolio_dd", 0.0)
        engine_n_live = ctx.get("engine_n_live", {})

        open_assets = [p.asset for p in open_positions]
        open_buckets = {}
        engine_exp: Dict[str, float] = {}
        bucket_exp: Dict[str, float] = {}
        for p in open_positions:
            engine_exp[p.engine_id] = engine_exp.get(p.engine_id, 0.0) + p.notional / max(equity, 1e-9)
            bucket_exp[p.bucket] = bucket_exp.get(p.bucket, 0.0) + p.notional / max(equity, 1e-9)
            open_buckets[p.bucket] = open_buckets.get(p.bucket, 0) + 1
        gross = sum(p.notional for p in open_positions) / max(equity, 1e-9)
        n_open = len(open_positions)
        open_set = set(open_assets)

        scored = cands.copy()
        scored["_utility"] = scored.apply(
            lambda r: self.utility(r, list(open_set), portfolio_dd), axis=1)
        scored = scored[scored["_utility"] > 0].sort_values("_utility", ascending=False)

        selected: List[Tuple[pd.Series, float]] = []
        for _, row in scored.iterrows():
            ok, _reason = self.constraints.check(
                asset=row["asset"], engine_id=row["engine_id"], bucket=row["bucket"],
                n_open=n_open, open_assets=open_set, bucket_count=open_buckets,
                engine_exposure=engine_exp, gross_exposure=gross,
            )
            if not ok:
                continue
            vol_cap = self.target_vol / max(float(row["expected_vol"]), 1e-6)
            n_live = 200 if not self.live_mode else int(engine_n_live.get(row["engine_id"], 0))
            f_kelly = max(0.0, 2.0 * float(row["p_success"]) - 1.0)  # proxy edge
            frac = multi_cap_size(
                f_kelly=f_kelly, n_live=n_live, regime_state="validated",
                vol_target_cap=vol_cap, drawdown_cap=1.0 - 4.0 * abs(min(0.0, portfolio_dd)),
                engine_exposure=engine_exp.get(row["engine_id"], 0.0),
                bucket_exposure=bucket_exp.get(row["bucket"], 0.0),
                gross_exposure=gross, caps=self.caps,
            ) * gov_mult
            if frac <= 1e-6:
                continue
            selected.append((row, frac))
            open_set.add(row["asset"])
            engine_exp[row["engine_id"]] = engine_exp.get(row["engine_id"], 0.0) + frac
            bucket_exp[row["bucket"]] = bucket_exp.get(row["bucket"], 0.0) + frac
            open_buckets[row["bucket"]] = open_buckets.get(row["bucket"], 0) + 1
            gross += frac
            n_open += 1
        return selected

    def as_hook(self):
        """Adapter pour PortfolioBacktester.allocator_hook."""
        def _hook(cands: pd.DataFrame, ctx: Dict):
            return self.allocate(cands, ctx)
        return _hook
