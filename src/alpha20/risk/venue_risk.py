"""
src/alpha20/risk/venue_risk.py — collatéral non sécurisé par venue (étape 4).

Non sécurisé = tout ce qui saute si la venue meurt : marge postée + PnL non
réglé + soldes spot on-exchange. Cap : 15 % du NAV par venue (profil
ALPHA20_LOW_RISK) ; le cap HL 25 % pré-déclaré du protocole xvenue reste un
souvenir — le cap alpha20 est plus strict et s'applique à toutes les venues.
"""
from __future__ import annotations

from typing import Dict, List

from src.alpha20.risk.global_governor import load_profile


def unsecured_by_venue(positions: List[Dict], balances: Dict[str, float],
                       nav_usdt: float) -> Dict[str, float]:
    """positions: [{venue, margin_usdt, unsettled_pnl_usdt}] ;
    balances: {venue: solde_on_exchange_usdt} → fraction de NAV par venue."""
    acc: Dict[str, float] = {}
    for p in positions:
        acc[p["venue"]] = (acc.get(p["venue"], 0.0)
                           + float(p.get("margin_usdt", 0.0))
                           + max(float(p.get("unsettled_pnl_usdt", 0.0)), 0.0))
    for v, bal in balances.items():
        acc[v] = acc.get(v, 0.0) + float(bal)
    return {v: x / nav_usdt for v, x in acc.items()} if nav_usdt else {}


def breaches(positions: List[Dict], balances: Dict[str, float],
             nav_usdt: float) -> Dict[str, float]:
    cap = load_profile().venue_unsecured_cap
    return {v: f for v, f in
            unsecured_by_venue(positions, balances, nav_usdt).items() if f > cap}
