"""
src/alpha20/costs/implementation_shortfall.py — TCA par décision (étape 7).

Mesure, pour chaque décision : prix de décision, prix d'arrivée, prix moyen
exécuté, spread payé, impact, délai, quantité non exécutée, adverse selection
à 1/5/30 min, coût total de rotation. Fournit aussi la règle de resize :

    resize seulement si gain net anticipé sur la durée minimale
                        > 3 × coût complet aller-retour

Le veto de réversion 72 h (commit 077171a) est le BASELINE à battre : cette
règle le remplace quand le TCA live fournit des coûts mesurés, pas avant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from src.alpha20 import load_config


@dataclass
class TCARecord:
    decision_ts: str
    sleeve: str
    symbol: str
    side: int                       # +1 / -1
    qty: float
    decision_px: float
    arrival_px: float
    avg_exec_px: Optional[float] = None
    filled_qty: float = 0.0
    spread_paid_bp: float = 0.0
    delay_s: float = 0.0
    post_px: Dict = field(default_factory=dict)   # {"1m": px, "5m": px, "30m": px}

    def metrics(self) -> Dict:
        m = {"unfilled_frac": 1.0 - (self.filled_qty / self.qty if self.qty else 0.0),
             "delay_s": self.delay_s, "spread_paid_bp": self.spread_paid_bp}
        if self.avg_exec_px:
            m["shortfall_bp"] = self.side * (self.avg_exec_px / self.decision_px - 1) * 1e4
            m["impact_bp"] = self.side * (self.avg_exec_px / self.arrival_px - 1) * 1e4
            for h, px in self.post_px.items():
                # adverse selection : le prix continue CONTRE nous après le fill
                m[f"adverse_{h}_bp"] = self.side * (self.avg_exec_px / px - 1) * 1e4
        return m


def full_roundtrip_cost_bp(maker_bp: float, taker_bp: float, spread_bp: float,
                           slippage_bp: float, n_legs: int = 2,
                           taker_frac: float = 1.0) -> float:
    """Coût complet A/R : commissions 2 sens × jambes + spread + slippage."""
    fee = (taker_frac * taker_bp + (1 - taker_frac) * maker_bp)
    return n_legs * 2 * (fee + slippage_bp) + spread_bp


def resize_worth_it(expected_net_gain_bp: float, rt_cost_bp: float) -> bool:
    """Règle unique de resize (étape 7) — seuil ×3 depuis configs/alpha20.yaml."""
    mult = float(load_config()["resize_rule"]["min_gain_over_rt_cost"])
    return expected_net_gain_bp > mult * rt_cost_bp
