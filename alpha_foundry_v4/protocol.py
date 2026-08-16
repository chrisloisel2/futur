from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScientificGates:
    min_dsr: float = 0.95
    max_pbo: float = 0.10
    min_sleeve_pf: float = 1.30
    min_capacity_usd: float = 200000.0
    max_pairwise_corr: float = 0.25
    min_independent_mechanisms: int = 10
    require_cost_x2_positive: bool = True
    require_delayed_entry_positive: bool = True
    require_top_contributors_removed_positive: bool = True
    require_pit_clean: bool = True
    require_same_sign_halves: bool = True
    require_recent_period_not_destructive: bool = True
    require_independent_forward: bool = True
    require_paper_live_positive: bool = True
    require_mechanism_confirmed: bool = True


DEFAULT_GATES = ScientificGates()
