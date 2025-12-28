from __future__ import annotations

from typing import Dict

import pandas as pd

from domain.risk.budgets import RiskBudgets
from domain.risk.scenarios import ScenarioState
from domain.state.books import BooksState
from domain.state.targets import TargetPositions
from pipeline.books.book_a_directional import BookADirectional, BookADirectionalConfig
from pipeline.books.book_b_convexity import BookBConvexity, BookBConvexityConfig
from pipeline.books.book_c_structural import BookCStructural, BookCStructuralConfig
from pipeline.books.allocator import MultiBookAllocator, AllocatorConfig


class MultiBookAlphaEngine:
    def __init__(self, config: Dict):
        self.book_a = BookADirectional(BookADirectionalConfig(**config.get("book_a", {})))
        self.book_b = BookBConvexity(BookBConvexityConfig(**config.get("book_b", {})))
        self.book_c = BookCStructural(BookCStructuralConfig(**config.get("book_c", {})))
        self.allocator = MultiBookAllocator(AllocatorConfig(**config.get("allocator", {})))

    def step(
        self,
        states: Dict[str, pd.Series],
        signals: Dict[str, dict],
        alloc: Dict,
        portfolio_state: Dict,
        risk_state: Dict,
        prev_books_state: BooksState | None,
        budgets: Dict,
        clusters: Dict[str, str],
        run_id: str,
        model_stack: str,
        feature_set: str,
    ) -> tuple[TargetPositions, BooksState, dict]:
        targets_a = []
        targets_b = []
        targets_c = []
        for sym, sig in signals.items():
            state = states.get(sym, pd.Series())
            targets_a += self.book_a.propose_targets(sym, state, sig, alloc, risk_state, budgets.get("book_a", {}))
            targets_b += self.book_b.propose_targets(sym, state, sig, alloc, risk_state, budgets.get("book_b", {}))
            targets_c += self.book_c.propose_targets(sym, state, alloc, risk_state, budgets.get("book_c", {}))
        tgt_positions, alloc_decision, books_state = self.allocator.merge_and_cap(
            targets_a,
            targets_b,
            targets_c,
            budgets,
            clusters,
            portfolio_state,
            risk_state,
            run_id,
            model_stack,
            feature_set,
        )
        return tgt_positions, books_state, alloc_decision.__dict__
