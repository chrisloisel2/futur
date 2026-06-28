"""
src/institutional/engines — Usine à opportunités.

Chaque moteur alpha est indépendant (source d'edge distincte) et émet le MÊME
contrat `Opportunity`. Aucun moteur n'importe un autre moteur ; ils communiquent
uniquement via les contrats (contracts.py) et le DecisionLedger.

Moteurs :
    TRM_TREND_LONG       — wrapper du TRM Fleet Long prouvé (alpha principal)
    TRM_TREND_INST       — moteur trend institutionnel (shadow parallèle)
    PULLBACK_LONG        — replis achetables (fréquence ↑, long-only)
    LIQUIDATION_REBOUND  — rebonds de capitulation (convexité)
    CARRY_BASIS          — funding/basis (rendement en marché plat, hedge only)
    CROSS_SECTIONAL_LONG — ranking top-k (diversification)
    EXIT_ENGINE          — sortie optimale (améliore tous les moteurs)
"""
from src.institutional.engines.base import AlphaEngine, EngineConfig
