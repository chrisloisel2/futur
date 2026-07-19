"""
src/alpha20/execution/hedge_coordinator.py — jambes appariées ATOMIQUES.

Contrat ALPHA20_LOW_RISK : aucune jambe nue au-delà de 30 s. Le coordinateur
planifie les paires (long + hedge) et fournit le chien de garde qui force la
fermeture de la jambe exposée si l'autre n'est pas remplie à temps.

V0 : planification et contrôle purs (pas d'ordres réels — le paper
d'exécution consomme ces plans). Le routage maker/taker fin arrive avec la
couche TCA (étape 7) dans smart_router/maker_model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from src.alpha20.risk.global_governor import load_profile


@dataclass
class PairPlan:
    pair_id: str
    legs: List[Dict]                 # [{venue, symbol, side, qty, kind}] ×2
    max_naked_s: int = 0

    def __post_init__(self):
        if len(self.legs) != 2:
            raise ValueError("PairPlan = exactement 2 jambes")
        if not self.max_naked_s:
            self.max_naked_s = load_profile().naked_leg_max_s


@dataclass
class PairState:
    plan: PairPlan
    fills: Dict = field(default_factory=dict)   # leg_idx -> {ts_ms, qty}

    def naked_age_s(self, now_ms: int) -> float:
        """Ancienneté de l'exposition nue (une jambe remplie, l'autre non)."""
        done = [i for i in (0, 1) if self.fills.get(i)]
        if len(done) != 1:
            return 0.0
        return (now_ms - self.fills[done[0]]["ts_ms"]) / 1000.0

    def must_unwind(self, now_ms: int) -> bool:
        """True → fermer immédiatement la jambe remplie (contrat 30 s violé)."""
        return self.naked_age_s(now_ms) > self.plan.max_naked_s
