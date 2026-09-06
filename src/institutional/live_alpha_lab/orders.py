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

# ── item 0.3 : le plafond adossé à la PROFONDEUR, pas au stock de positions ──
#
# Le plafond ci-dessus est adossé à l'open interest, qui est un stock de
# positions ouvertes et non une profondeur de carnet. Mesuré sur le forward
# (P1_CONTROL, 1 634 ordres) il a mordu 16 fois, soit 1,0 % : il ne mord
# jamais. Pendant ce temps, la profondeur médiane au meilleur limite du
# frozen-50 vaut ~1 048 $ (ARUSDT : 53 $) et 18,7 % des ordres exécutés la
# dépassent. Près d'un ordre sur cinq n'aurait pas pu être rempli au prix
# supposé, et toute mesure en hérite tant que ce n'est pas corrigé.
#
# POURQUOI LA FRACTION VAUT 1,0 ET NON UNE VALEUR PLUS PETITE
#     Le notionnel affiché au meilleur limite est EXACTEMENT la taille pour
#     laquelle le spread coté a été observé. En deçà, le modèle de coût (mid
#     moins deux bps) est adossé à une observation ; au-delà, il ne l'est plus.
#     Le plafond n'est donc pas un choix de prudence à calibrer, c'est la
#     frontière du domaine de validité du modèle. La rendre plus petite serait
#     défendable ; la rendre plus grande serait inventer de la liquidité.
MAX_FILL_FRACTION_OF_DEPTH = 1.0

CAP_POLICY_OPEN_INTEREST = "OPEN_INTEREST"
CAP_POLICY_TOP_OF_BOOK = "TOP_OF_BOOK"

# LA FRONTIÈRE DE SEGMENT, déclarée plutôt que subie.
#
# Changer la règle de fill en cours de route mélange deux régimes d'exécution
# dans une même courbe d'équité. On ne réécrit donc pas le passé : les ordres
# antérieurs à cette date gardent la règle sous laquelle ils ont été produits,
# et chaque ordre porte la politique qui l'a plafonné (`cap_policy`). Toute
# lecture d'une série qui traverse cette date doit la segmenter.
DEPTH_CAP_EFFECTIVE_FROM = "2026-09-07T00:00:00+00:00"


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
    # item 0.3 : quelle règle a plafonné cet ordre, et de combien. Porté sur
    # l'ordre lui-même pour qu'une série qui traverse la frontière de segment
    # reste lisible sans avoir à deviner la date.
    cap_policy: Optional[str] = None
    cap_notional_usd: Optional[float] = None
    refused_notional_usd: float = 0.0


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
    un fill bloqué par une valeur inventée).

    Règle HISTORIQUE (open interest). Conservée telle quelle : elle a produit
    tous les ordres antérieurs à `DEPTH_CAP_EFFECTIVE_FROM` et les réécrire
    rendrait la courbe d'équité incomparable à elle-même."""
    if mark.liquidity_notional is None or mark.price <= 0:
        return None
    return (mark.liquidity_notional * MAX_FILL_FRACTION_OF_LIQUIDITY) / mark.price


def depth_cap_quantity(mark, depth_notional_usd: Optional[float]) -> Optional[float]:
    """Quantité max exécutable d'après la PROFONDEUR observée au meilleur limite.

    `None` quand aucune sonde n'existe pour ce symbole — fail-open, comme la
    règle historique : un plafond inventé serait pire qu'aucun plafond. Le
    rapport de capacité compte séparément les symboles sans sonde, pour que
    « pas plafonné » ne se lise jamais comme « mesuré et large ».
    """
    if depth_notional_usd is None or mark.price <= 0:
        return None
    if not (depth_notional_usd > 0):
        return None
    return (float(depth_notional_usd) * MAX_FILL_FRACTION_OF_DEPTH) / mark.price


def cap_policy_for(as_of) -> str:
    """La politique en vigueur à cette date. La frontière est une donnée, pas
    une branche à retrouver dans le code."""
    import pandas as pd

    stamp = pd.Timestamp(as_of)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return (CAP_POLICY_TOP_OF_BOOK
            if stamp >= pd.Timestamp(DEPTH_CAP_EFFECTIVE_FROM)
            else CAP_POLICY_OPEN_INTEREST)
