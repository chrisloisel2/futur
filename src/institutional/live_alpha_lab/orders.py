"""
src/institutional/live_alpha_lab/orders.py
─────────────────────────────────────────────────────────────────────────────
ShadowOrder/ShadowFill -- schéma complet d'exécution shadow (item P0.2,
phase CLOSE THE EXECUTION LOOP).

Un ShadowOrder correspond à UNE tentative d'exécution soumise à un `as_of`
donné par portfolio.step() :
  - FILLED si tout le delta demandé a pu être exécuté ce step ;
  - PARTIALLY_FILLED si un plafond de liquidité (fraction de l'open
    interest notionnel courant, cf marks.MarkQuote.liquidity_notional) a
    limité le fill à ce step ;
  - REJECTED si aucun mark n'était disponible (jamais un fill inventé).

Le manque non comblé d'un ordre PARTIALLY_FILLED n'est PAS reporté comme
un ordre "en attente" à reprendre plus tard : ShadowExecutionAdapter.
cancel_order/replace_order n'existent délibérément pas (pas de concept
d'ordre persistant modifiable dans un shadow book). Le step SUIVANT
recalcule un delta frais (target - position courante, qui reflète déjà le
fill partiel de ce step) et soumet un NOUVEL ordre pour le reliquat. Ce
choix élimine tout état "ordre en vol" à restaurer après un restart : la
position (persistée) EST la trace de ce qui a été rempli, ce qui garantit
no-double-counting et restart-safety sans mécanisme séparé.

Chaque exécution (même partielle) produit aussi une ShadowFill immuable,
append-only -- la maille la plus fine pour la reconstruction de trace
(item P0.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ORDER_STATUSES = ("SUBMITTED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELLED", "EXPIRED")

# Fraction de l'open interest notionnel courant qu'un seul fill (un seul
# step) peut consommer -- une position visée trop grosse par rapport à la
# liquidité disponible sur un alt illiquide se remplit sur plusieurs steps,
# comme le ferait un vrai carnet. Valeur conservatrice, documentée, pas
# calibrée sur une vraie profondeur de carnet (qu'on n'a pas) : c'est un
# proxy honnête, pas une simulation de microstructure réelle.
MAX_FILL_FRACTION_OF_LIQUIDITY = 0.002


@dataclass
class ShadowOrder:
    order_id: str
    intent_id: str
    signal_id: str
    alpha_id: str
    portfolio_id: str
    timestamp_decision: str
    timestamp_submit: str
    timestamp_fill: Optional[str]
    symbol: str
    side: str                      # "BUY" | "SELL"
    requested_quantity: float      # >= 0 (abs) ; le signe est dans `side`
    filled_quantity: float         # >= 0 (abs)
    remaining_quantity: float      # >= 0 (abs)
    requested_notional: float      # >= 0 (abs), au mark_price_at_decision
    fill_price: Optional[float]
    mark_price_at_decision: float
    spread_bps: float
    slippage_bps: float
    fee_bps: float
    fee_amount: float
    status: str
    # item P0.4 : renseigné par portfolio.step() (pas par l'adapter -- le
    # concept "horizon d'alpha expiré" appartient à la couche portfolio, pas
    # à l'exécution) uniquement pour un ordre qui RÉDUIT une position.
    # "ALPHA_HORIZON_EXPIRY" si PLUS AUCUN intent actif ne visait cet
    # instrument (tous ceux qui le visaient ont expiré) ; "TARGET_CHANGE"
    # pour toute autre réduction (signal inversé, screen, cap de risque,
    # arbitrage de dedup -- catch-all honnête, pas une fausse précision).
    exit_reason: Optional[str] = None


@dataclass
class ShadowFill:
    fill_id: str
    order_id: str
    intent_id: str
    signal_id: str
    alpha_id: str
    portfolio_id: str
    timestamp: str
    symbol: str
    quantity: float                # signé (delta position)
    fill_price: float
    fee_usd: float
    mark_source: str
    mark_stale: bool


def liquidity_cap_quantity(mark) -> Optional[float]:
    """Quantité max exécutable en un seul fill d'après le proxy de liquidité
    du mark. None = pas de plafond (source sans proxy -- fail-open, jamais
    un fill bloqué par une valeur inventée)."""
    if mark.liquidity_notional is None or mark.price <= 0:
        return None
    return (mark.liquidity_notional * MAX_FILL_FRACTION_OF_LIQUIDITY) / mark.price
