"""
research/edge_factory/multileg_engine/backtest_result.py — MultiLegBacktestResult (interface 5/5).

Ne recalcule aucun gate : ce format alimente directement
src.alpha20.validation.promotion_gate.gate_sleeve() et gate_research(), qui
implémentent déjà PF, coûts×2, DSR (Bailey & López de Prado) et PBO (CSCV).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from src.alpha20.contracts import GateResult
from src.alpha20.validation.promotion_gate import gate_research, gate_sleeve


@dataclass
class MultiLegBacktestResult:
    trades: pd.DataFrame
    pnl_daily: pd.Series
    per_year: Dict[str, float]
    net_events: pd.Series               # entrée directe de gate_sleeve()
    net_events_x2: pd.Series             # entrée directe de gate_sleeve()
    returns_for_dsr: pd.Series           # entrée directe de gate_research()
    trials_matrix: Optional[pd.DataFrame] = None   # entrée de pbo_cscv(), None si pas de grille
    meta: Dict = field(default_factory=dict)       # provenance, hypothèses de coût, etc.

    def run_sleeve_gate(self) -> List[GateResult]:
        years = sorted(self.per_year)
        recent_year_net = self.per_year[years[-1]] if years else -1.0
        return gate_sleeve(self.net_events, self.net_events_x2, recent_year_net)

    def run_research_gate(self, n_trials: int, corr_with_kept: float = 0.0,
                          capacity_eur: float = 0.0) -> List[GateResult]:
        return gate_research(self.returns_for_dsr, n_trials,
                             trials_matrix=self.trials_matrix,
                             corr_with_kept=corr_with_kept,
                             capacity_eur=capacity_eur)
