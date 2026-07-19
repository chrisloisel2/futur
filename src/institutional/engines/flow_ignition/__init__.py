"""FLOW_IGNITION — expansion d'OI + flux taker = continuation (argent frais).

Mécanisme OPPOSÉ-complémentaire à LIQ_CASCADE : la cascade trade la
COMPRESSION (OI purgé → rebond) ; l'ignition trade l'EXPANSION (OI qui monte
avec le prix + taker acheteur = nouvelles positions, pas du short-covering →
continuation sur heures). Littérature futures classique, jamais tradée ici.
Données : metrics 5-min Vision (49 actifs, 2021-2026).
"""
from src.institutional.engines.flow_ignition.detector import (  # noqa: F401
    IgnitionConfig, detect_ignitions,
)
