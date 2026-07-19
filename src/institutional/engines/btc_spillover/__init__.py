"""BTC_SPILLOVER — lead-lag BTC → alts retardataires (propagation).

Mécanisme distinct de toutes les jambes existantes : quand BTC fait un thrust
horaire fort, les alts à beta positif rattrapent avec un retard de minutes à
heures (liquidité plus faible, flux séquentiels). On achète le RETARDATAIRE
(pas BTC, pas l'alt qui a déjà bougé). Données : metrics 5-min Vision.
"""
from src.institutional.engines.btc_spillover.detector import (  # noqa: F401
    SpilloverConfig, detect_spillovers,
)
