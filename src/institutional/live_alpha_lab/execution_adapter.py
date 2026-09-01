"""
src/institutional/live_alpha_lab/execution_adapter.py
─────────────────────────────────────────────────────────────────────────────
ShadowExecutionAdapter expose la MÊME interface qu'un futur RealExecutionAdapter
(item P0.2, phase CLOSE THE EXECUTION LOOP) -- submit_order/cancel_order/
replace_order/get_open_orders/get_positions/get_fills/get_balance/reconcile
-- pour que le code appelant (portfolio.py::step, désormais le SEUL chemin
d'exécution -- plus d'appel direct à shadow_execute() en dehors de cet
adapter) puisse un jour être pointé sur un adapter réel SANS changer sa
propre logique.

`submit_order` supporte un VRAI remplissage partiel (pas 100% codé en dur) :
plafonné par `orders.liquidity_cap_quantity()` (proxy open interest, voir
orders.py). `cancel_order`/`replace_order` lèvent NotImplementedError
explicitement -- pas de concept d'ordre en carnet à annuler/remplacer pour
un fill shadow instantané (voir orders.py pour la justification du modèle
"un ordre = une tentative par step").

AUCUN ordre réel envoyé nulle part dans ce fichier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.institutional.live_alpha_lab.marks import get_mark
from src.institutional.live_alpha_lab.orders import (
    ShadowFill, ShadowOrder, liquidity_cap_quantity,
)
from src.institutional.live_alpha_lab.portfolio import (
    FIXED_SLIPPAGE_BPS, TAKER_FEE_BPS, shadow_execute,
)


@dataclass
class ExecutionAdapter:
    """Interface commune Shadow/Real -- méthodes que les DEUX doivent
    implémenter. Ne pas instancier directement."""

    def submit_order(self, **kwargs) -> Tuple[ShadowOrder, Optional[ShadowFill]]:
        raise NotImplementedError

    def cancel_order(self, order_id: str):
        raise NotImplementedError

    def replace_order(self, order_id: str, **kwargs):
        raise NotImplementedError

    def get_open_orders(self) -> List[ShadowOrder]:
        raise NotImplementedError

    def get_positions(self) -> Dict[str, float]:
        raise NotImplementedError

    def get_fills(self) -> List[ShadowFill]:
        raise NotImplementedError

    def get_balance(self) -> Dict[str, float]:
        raise NotImplementedError

    def reconcile(self, expected_positions: Dict[str, float]) -> bool:
        raise NotImplementedError


@dataclass
class ShadowExecutionAdapter(ExecutionAdapter):
    """AUCUN ordre réel. `submit_order` calcule un fill (partiel ou complet)
    via `shadow_execute()` (le même modèle de coût que portfolio.py::step)
    et l'enregistre localement -- pas de carnet d'ordres réel à annuler/
    remplacer.

    Bookkeeping interne (`_orders`/`_fills`/`_positions`) = auto-suivi POUR
    CE RUN uniquement (utile pour `reconcile()`), PAS la source de vérité
    durable -- celle-ci reste PortfolioState (state.json), que portfolio.py
    persiste explicitement à chaque step (mêmes garanties restart que
    positions/equity_curve)."""
    _orders: List[ShadowOrder] = field(default_factory=list)
    _fills: List[ShadowFill] = field(default_factory=list)
    _positions: Dict[str, float] = field(default_factory=dict)   # quantité, PAS notional
    _seq: int = 0

    def _next_id(self, portfolio_id: str, symbol: str, as_of: pd.Timestamp) -> str:
        self._seq += 1
        return f"{portfolio_id}:{symbol}:{as_of.isoformat()}:{self._seq}"

    def submit_order(self, *, portfolio_id: str, alpha_id: str, intent_id: str,
                     signal_id: str, symbol: str, delta_quantity: float,
                     as_of: pd.Timestamp, timestamp_decision: str,
                     mark=None) -> Tuple[ShadowOrder, Optional[ShadowFill]]:
        """`delta_quantity` = delta signé DEMANDÉ (target - position
        actuelle). Retourne (order, fill) -- fill est None si status=REJECTED
        (aucun mark dispo, jamais un fill inventé).

        `mark` : MarkQuote déjà résolu par l'appelant (portfolio.step() en
        récupère toujours un pour décider s'il y a un prix disponible AVANT
        de soumettre un ordre) -- évite une deuxième lecture I/O du même
        (instrument, as_of) et, en test, respecte le monkeypatch appliqué
        sur portfolio_mod.get_mark (un import direct ici créerait un
        deuxième binding indépendant que monkeypatch.setattr ne verrait pas).
        Si omis (appel direct de l'adapter hors step()), résolu ici via
        get_mark() -- un RealExecutionAdapter n'aurait pas ce paramètre du
        tout, il obtient son prix de l'exchange."""
        order_id = self._next_id(portfolio_id, symbol, as_of)
        side = "BUY" if delta_quantity > 0 else "SELL"
        requested_quantity = abs(delta_quantity)
        ts_submit = as_of.isoformat()

        if mark is None:
            mark = get_mark(symbol, as_of)
        if mark is None:
            order = ShadowOrder(
                order_id=order_id, intent_id=intent_id, signal_id=signal_id,
                alpha_id=alpha_id, portfolio_id=portfolio_id,
                timestamp_decision=timestamp_decision, timestamp_submit=ts_submit,
                timestamp_fill=None, symbol=symbol, side=side,
                requested_quantity=requested_quantity, filled_quantity=0.0,
                remaining_quantity=requested_quantity, requested_notional=0.0,
                fill_price=None, mark_price_at_decision=0.0,
                spread_bps=0.0, slippage_bps=FIXED_SLIPPAGE_BPS, fee_bps=TAKER_FEE_BPS,
                fee_amount=0.0, status="REJECTED",
            )
            self._orders.append(order)
            return order, None

        requested_notional = requested_quantity * mark.price
        cap_qty = liquidity_cap_quantity(mark)
        fillable_quantity = requested_quantity if cap_qty is None else min(requested_quantity, cap_qty)
        signed_fillable = fillable_quantity if delta_quantity > 0 else -fillable_quantity

        fill_record = None
        if abs(signed_fillable * mark.price) >= 1e-6:
            f = shadow_execute(signed_fillable, symbol, mark)
            fill_id = f"{order_id}:F0"
            fill_record = ShadowFill(
                fill_id=fill_id, order_id=order_id, intent_id=intent_id, signal_id=signal_id,
                alpha_id=alpha_id, portfolio_id=portfolio_id, timestamp=ts_submit, symbol=symbol,
                quantity=f.delta_quantity, fill_price=f.fill_price, fee_usd=f.fee_usd,
                mark_source=f.mark_source, mark_stale=f.mark_stale,
            )
            self._fills.append(fill_record)
            self._positions[symbol] = self._positions.get(symbol, 0.0) + f.delta_quantity
            filled_quantity = abs(f.delta_quantity)
            fee_amount = f.fee_usd
            fill_price = f.fill_price
        else:
            filled_quantity = 0.0
            fee_amount = 0.0
            fill_price = None

        remaining_quantity = requested_quantity - filled_quantity
        # spread_bps : pas de bid/ask réel dans derivatives_raw (seulement
        # mark_price) -- modélisé comme 2x notre hypothèse de slippage
        # (round-trip), documenté explicitement, jamais présenté comme une
        # mesure empirique du carnet.
        status = (
            "FILLED" if remaining_quantity <= 1e-9 else
            "PARTIALLY_FILLED" if filled_quantity > 0 else
            "SUBMITTED"
        )
        order = ShadowOrder(
            order_id=order_id, intent_id=intent_id, signal_id=signal_id,
            alpha_id=alpha_id, portfolio_id=portfolio_id,
            timestamp_decision=timestamp_decision, timestamp_submit=ts_submit,
            timestamp_fill=ts_submit if filled_quantity > 0 else None,
            symbol=symbol, side=side,
            requested_quantity=requested_quantity, filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity, requested_notional=requested_notional,
            fill_price=fill_price, mark_price_at_decision=mark.price,
            spread_bps=FIXED_SLIPPAGE_BPS * 2, slippage_bps=FIXED_SLIPPAGE_BPS,
            fee_bps=TAKER_FEE_BPS, fee_amount=fee_amount, status=status,
        )
        self._orders.append(order)
        return order, fill_record

    def cancel_order(self, order_id: str):
        raise NotImplementedError(
            "ShadowExecutionAdapter remplit instantanément (jusqu'au plafond de liquidité) "
            "-- pas d'ordre en carnet à annuler. RealExecutionAdapter (jamais implémenté "
            "ici, aucun ordre réel) devra fournir ceci."
        )

    def replace_order(self, order_id: str, **kwargs):
        raise NotImplementedError(
            "Idem cancel_order() -- pas de concept d'ordre en attente modifiable en mode "
            "shadow (voir orders.py : un ordre = une tentative par step, le reliquat "
            "d'un PARTIALLY_FILLED est repris par un NOUVEL ordre au step suivant)."
        )

    def get_open_orders(self) -> List[ShadowOrder]:
        """Aucun ordre ne reste "ouvert" entre deux appels à submit_order()
        (voir orders.py) -- toujours vide pour un adapter shadow. Le champ
        existe pour la parité d'interface avec un futur RealExecutionAdapter."""
        return []

    def get_positions(self) -> Dict[str, float]:
        return dict(self._positions)

    def get_fills(self) -> List[ShadowFill]:
        return list(self._fills)

    def get_balance(self) -> Dict[str, float]:
        raise NotImplementedError(
            "get_balance() reflète un compte réel (cash/marge exchange) -- le portfolio "
            "shadow suit son propre cash simulé dans PortfolioState, pas un concept "
            "'balance' d'exchange. Voir portfolio.PortfolioState.cash pour l'équivalent shadow."
        )

    def reconcile(self, expected_positions: Dict[str, float]) -> bool:
        """Un adapter shadow EST son propre livre de vérité (pas d'exchange
        externe à réconcilier contre) -- vérifie juste l'auto-cohérence
        entre son bookkeeping interne et les positions officielles tenues
        par PortfolioState."""
        return self._positions == expected_positions
