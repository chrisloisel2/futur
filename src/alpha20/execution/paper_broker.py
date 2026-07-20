"""
src/alpha20/execution/paper_broker.py — BROKER PAPER unique du tournoi.

Toute décision de tout runner passe par ici — aucun accès direct à un
exchange. Simule : bid/ask (spread ASSUMED tant qu'aucun L2 n'alimente le bus
— étiqueté), latence, remplissages complets/partiels, ordres non remplis,
slippage, commission maker/taker (fee_registry — réel si snapshot signé,
sinon ASSUMED), legging (jambes appariées — tout dépareillage intra-cycle est
DÉNOUÉ dans le même cycle : en paper périodique les cycles sont >> 30 s, donc
aucune jambe nue ne peut survivre au-delà d'un cycle par construction), rejets
et liquidations (gate du governor AVANT tout ordre).

Six scénarios calculés SIMULTANÉMENT à chaque exécution — robustesse, jamais
tuning : observed, cost_x1.5, cost_x2, latency_hostile, partial_fills,
venue_outage. Seul `observed` met à jour la position réelle du runner ; les
cinq autres sont journalisés comme télémétrie de robustesse.

Reste NotImplementedError pour tout ordre RÉEL par construction : ce module
ne contient aucun client d'exchange, uniquement de l'arithmétique.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from src.alpha20.costs.fee_registry import effective_costs
from src.alpha20.execution.hedge_coordinator import PairPlan, PairState
from src.alpha20.tournament.market_bus import MarketSnapshot

HALF_SPREAD_BP_DEFAULT = 1.0        # ASSUMED tant que le bus n'a pas de L2 réel

SCENARIOS: Dict[str, dict] = {
    "observed":        {"cost_mult": 1.0, "latency_ms": 150, "fill_frac": 1.0,
                        "adverse_bp": 0.0},
    "cost_x1.5":       {"cost_mult": 1.5, "latency_ms": 150, "fill_frac": 1.0,
                        "adverse_bp": 0.0},
    "cost_x2":         {"cost_mult": 2.0, "latency_ms": 150, "fill_frac": 1.0,
                        "adverse_bp": 0.0},
    "latency_hostile": {"cost_mult": 1.0, "latency_ms": 3000, "fill_frac": 1.0,
                        "adverse_bp": 5.0},
    "partial_fills":   {"cost_mult": 1.0, "latency_ms": 150, "fill_frac": 0.55,
                        "adverse_bp": 0.0},
    "venue_outage":    {"cost_mult": 1.0, "latency_ms": None, "fill_frac": 0.0,
                        "adverse_bp": 0.0, "rejected": True},
}


@dataclass
class Order:
    runner_id: str
    symbol: str
    venue: str
    side: int                        # +1 buy / -1 sell
    notional_usdt: float
    kind: str = "perp"               # perp | spot | quarterly
    urgency: str = "taker"           # taker | maker
    leg_of: Optional[str] = None     # pair_id si jambe d'une paire appariée
    is_exit: bool = False            # réduit le risque — jamais bloqué par kill


@dataclass
class Fill:
    scenario: str
    filled_notional: float
    avg_price: float
    fee_usdt: float
    fee_bp: float
    fee_source: str
    slippage_bp: float
    spread_source: str
    latency_ms: Optional[float]
    rejected: bool
    reject_reason: Optional[str] = None
    unfilled_notional: float = 0.0


class PaperBroker:
    """Aucun état de marché propre : reçoit un MarketSnapshot déjà construit
    (le bus), ne fait AUCUN appel réseau — garantit que tous les runners du
    cycle voient le même prix."""

    def execute(self, order: Order, snapshot: MarketSnapshot,
               risk_state: str = "risk_on") -> Dict[str, Fill]:
        if risk_state == "kill" and not order.is_exit:
            f = Fill("observed", 0.0, 0.0, 0.0, 0.0, "n/a", 0.0, "n/a", None,
                     True, "kill_switch_active", order.notional_usdt)
            return {"observed": f}
        mid = snapshot.price(order.symbol)
        if mid is None:
            f = Fill("observed", 0.0, 0.0, 0.0, 0.0, "n/a", 0.0, "n/a", None,
                     True, "no_price_in_snapshot", order.notional_usdt)
            return {s: f for s in ("observed",)}
        costs = effective_costs(order.venue, order.symbol)
        out = {}
        for name, sc in SCENARIOS.items():
            if sc.get("rejected"):
                out[name] = Fill(name, 0.0, 0.0, 0.0, 0.0, costs.source, 0.0,
                                 "assumed", None, True, "venue_outage",
                                 order.notional_usdt)
                continue
            half_spread = HALF_SPREAD_BP_DEFAULT
            fee_bp = costs.taker_bp if order.urgency == "taker" else costs.maker_bp
            slip_bp = (costs.slippage_bp or 0.0)
            total_bp = (fee_bp + slip_bp + half_spread) * sc["cost_mult"]
            adverse = sc["adverse_bp"] / 1e4 * order.side
            px = mid * (1 + order.side * total_bp / 1e4 + adverse)
            filled = order.notional_usdt * sc["fill_frac"]
            fee_usdt = filled * fee_bp / 1e4 * sc["cost_mult"]
            out[name] = Fill(name, round(filled, 2), round(px, 8),
                             round(fee_usdt, 6), round(fee_bp * sc["cost_mult"], 3),
                             costs.source, round((slip_bp + half_spread)
                                                 * sc["cost_mult"], 3),
                             "assumed_default", sc["latency_ms"], False, None,
                             round(order.notional_usdt - filled, 2))
        return out

    def execute_pair(self, order_a: Order, order_b: Order,
                     snapshot: MarketSnapshot,
                     risk_state: str = "risk_on") -> dict:
        """Paire appariée (ex. spot+quarterly, spot+perp). Tout dépareillage
        intra-cycle (une jambe remplie, l'autre non) est DÉNOUÉ immédiatement
        — en paper périodique (cycles >> 30 s), une jambe nue ne peut jamais
        survivre au-delà d'un cycle par construction : la paire entière est
        rejetée plutôt que de laisser une exposition non couverte."""
        fa = self.execute(order_a, snapshot, risk_state)["observed"]
        fb = self.execute(order_b, snapshot, risk_state)["observed"]
        plan = PairPlan(f"{order_a.runner_id}:{order_a.symbol}:pair",
                        [{"venue": order_a.venue, "symbol": order_a.symbol,
                          "side": order_a.side, "qty": order_a.notional_usdt,
                          "kind": order_a.kind},
                         {"venue": order_b.venue, "symbol": order_b.symbol,
                          "side": order_b.side, "qty": order_b.notional_usdt,
                          "kind": order_b.kind}])
        st = PairState(plan)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if not fa.rejected:
            st.fills[0] = {"ts_ms": now_ms, "qty": fa.filled_notional}
        if not fb.rejected:
            st.fills[1] = {"ts_ms": now_ms, "qty": fb.filled_notional}
        mismatched = fa.rejected != fb.rejected
        if mismatched:
            # dénoue la jambe remplie : reject logique de la paire entière
            return {"leg_a": fa, "leg_b": fb, "pair_status": "leg_mismatch_unwound",
                    "naked_age_s": 0.0}
        status = "both_filled" if not fa.rejected else "both_rejected"
        return {"leg_a": fa, "leg_b": fb, "pair_status": status,
                "naked_age_s": st.naked_age_s(now_ms)}
