"""PREMIUM_DISLOCATION — dislocations du premium perp vs index (microstructure).

Mécanisme distinct de LIQ_CASCADE (OI) et CROWDING_REVERSAL (positionnement) :
le premium index mesure la pression d'achat/vente sur le PERP relativement au
spot. Un premium profondément négatif = vente forcée sur le perp (capitulation
leveraged) → l'arbitrage funding/basis ramène le perp vers l'index → long.
Données : premiumIndexKlines 5m Binance Vision (2021-2026, backfillées).
"""
from src.institutional.engines.premium_dislocation.detector import (  # noqa: F401
    PremiumConfig, detect_premium_dislocations,
)
