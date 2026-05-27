from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.risk.budgets import RiskBudgets


@dataclass
class RouterConfig:
    router_top_k: int = 3
    router_min_score: float = 0.0
    router_max_assets: int = 5
    asset_weight_cap: float = 0.5


class PortfolioRouter:
    def __init__(self, config: RouterConfig):
        self.config = config

    def rank_assets(self, signals: Dict[str, dict], states: Dict[str, pd.Series], costs_proxy: Dict[str, float]) -> Dict[str, float]:
        scores = {}
        for sym, sig in signals.items():
            q50 = sig.get("q50", sig.get("quantiles", {}).get("Q50", 0))
            cost = costs_proxy.get(sym, 0.0)
            score = q50 - cost
            scores[sym] = score
        return scores

    def allocate(self, scores: Dict[str, float], budgets: RiskBudgets, clusters: Dict[str, str]) -> Dict[str, float]:
        sorted_syms = [k for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True) if v >= self.config.router_min_score]
        sorted_syms = sorted_syms[: self.config.router_max_assets]
        weights: Dict[str, float] = {}
        remaining = 1.0
        cluster_usage: Dict[str, float] = {}
        for sym in sorted_syms:
            cluster = clusters.get(sym, "default")
            cap_cluster = budgets.meta_cluster_caps.get(cluster, 1.0)
            max_w = min(self.config.asset_weight_cap, budgets.meta_max_concentration, cap_cluster - cluster_usage.get(cluster, 0.0), remaining)
            if max_w <= 0:
                continue
            w = min(max_w, remaining)
            weights[sym] = w
            cluster_usage[cluster] = cluster_usage.get(cluster, 0.0) + w
            remaining -= w
            if remaining <= 0:
                break
        return weights
