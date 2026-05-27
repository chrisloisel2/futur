from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import json
import pandas as pd

from domain.state.targets import TargetPosition, TargetPositions
from domain.state.allocator import AllocatorDecision
from domain.risk.budgets import RiskBudgets
from domain.state.books import BooksState


@dataclass
class AllocatorConfig:
    max_total_gross_exposure_usd: float = 250_000.0
    max_cluster_exposure_usd: float = 150_000.0
    max_asset_weight: float = 0.5
    min_expected_utility: float = 0.0
    max_cost_bps: float = 10.0
    rebalance_cooldown_seconds: int = 60


class MultiBookAllocator:
    def __init__(self, config: AllocatorConfig):
        self.config = config

    def merge_and_cap(
        self,
        targets_a: List[TargetPosition],
        targets_b: List[TargetPosition],
        targets_c: List[TargetPosition],
        budgets: Dict,
        clusters: Dict[str, str],
        portfolio_state: Dict,
        risk_state: Dict,
        run_id: str,
        model_stack: str,
        feature_set: str,
    ) -> tuple[TargetPositions, AllocatorDecision, BooksState]:
        all_targets = targets_a + targets_b + targets_c
        filtered: List[TargetPosition] = []
        book_summary: Dict[str, Dict[str, float]] = {}
        cluster_usage: Dict[str, float] = {}
        reasons: List[str] = []
        for tgt in all_targets:
            if tgt.expected_utility < self.config.min_expected_utility:
                reasons.append(f"drop_low_utility_{tgt.symbol}")
                continue
            if tgt.cost_estimate_bps > self.config.max_cost_bps:
                reasons.append(f"drop_cost_{tgt.symbol}")
                continue
            cluster = clusters.get(tgt.symbol, "default")
            current_cluster = cluster_usage.get(cluster, 0.0)
            if current_cluster + tgt.notional_usd > self.config.max_cluster_exposure_usd:
                reasons.append(f"cluster_cap_{tgt.symbol}")
                continue
            cluster_usage[cluster] = current_cluster + tgt.notional_usd
            filtered.append(tgt)
            book_summary.setdefault(tgt.book, {"gross_usd": 0.0, "net_usd": 0.0})
            book_summary[tgt.book]["gross_usd"] += abs(tgt.notional_usd)
            book_summary[tgt.book]["net_usd"] += tgt.notional_usd if tgt.side == "LONG" else -tgt.notional_usd
        alloc_decision = AllocatorDecision(
            event_time=pd.Timestamp.utcnow(),
            book_weights={"book_a": 0.0, "book_b": 0.0, "book_c": 0.0},
            book_caps_applied=[],
            cluster_caps_applied=[],
            dropped_targets=[r for r in reasons if r.startswith("drop")],
            reasons=reasons,
        )
        books_state = BooksState(books={}, active_books=list({t.book for t in filtered}), last_rebalance_time=pd.Timestamp.utcnow())
        target_positions = TargetPositions(
            event_time=pd.Timestamp.utcnow(),
            run_id=run_id,
            model_stack=model_stack,
            feature_set=feature_set,
            targets=filtered,
            book_summary=book_summary,
            allocator_reasons=reasons,
        )
        return target_positions, alloc_decision, books_state
