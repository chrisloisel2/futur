"""
src/alpha20/risk/stress_engine.py — stress tests obligatoires (étape 4).

Applique les 10 scénarios du profil à un état de portefeuille déclaratif et
retourne la perte estimée par scénario en fraction de NAV. Un sleeve n'est
promouvable que si TOUS les scénarios restent au-dessus du kill (−2,5 %).

État attendu (déclaratif, pas de dépendance aux moteurs) :
  {nav_usdt, gross_usdt, net_delta_usdt, borrow_usdt, funding_ann_usdt,
   spread_cost_bp_gross, venues: {name: unsecured_usdt},
   stable_collateral_usdt, legs: [{notional_usdt, hedged}]}
"""
from __future__ import annotations

from typing import Dict


def run_all(state: Dict) -> Dict[str, float]:
    nav = float(state["nav_usdt"])
    gross = float(state.get("gross_usdt", 0.0))
    delta = float(state.get("net_delta_usdt", 0.0))
    borrow = float(state.get("borrow_usdt", 0.0))
    fund_ann = float(state.get("funding_ann_usdt", 0.0))
    spread_bp = float(state.get("spread_cost_bp_gross", 2.0))
    naked = sum(float(l["notional_usdt"]) for l in state.get("legs", [])
                if not l.get("hedged", True))
    worst_venue = max([float(v) for v in
                       state.get("venues", {}).values()] or [0.0])
    stable = float(state.get("stable_collateral_usdt", 0.0))

    losses = {
        # 30 j de funding inversé sur le gross porteur
        "funding_flip": 2 * fund_ann * (30 / 365.0),
        # 30 j de surcoût d'emprunt ×4
        "borrow_x4": 3 * borrow * 0.08 * (30 / 365.0),
        # liquidation du gross avec spread ×5
        "spread_x5": gross * 5 * spread_bp / 1e4,
        # gap 20 % sur le delta net + jambes nues
        "gap_20pct": abs(delta) * 0.20 + naked * 0.20,
        # vol ×3 ≈ triple du spread de sortie + gap 5 % sur delta
        "vol_x3": gross * 3 * spread_bp / 1e4 + abs(delta) * 0.05,
        "stablecoin_down_10pct": stable * 0.10,
        # venue morte 24 h : collatéral non sécurisé de la pire venue à risque
        "venue_down_24h": worst_venue,
        # liquidation forcée d'une jambe : pire jambe × (gap 3 % + spread ×5)
        "leg_liquidated": max([float(l["notional_usdt"]) for l in
                               state.get("legs", [])] or [0.0])
                          * (0.03 + 5 * spread_bp / 1e4),
        # données stale : on porte le delta 1 h sans hedge (2 % adverse)
        "stale_data": abs(delta) * 0.02,
        # fills partiels : moitié du gross re-exécutée en taker spread ×2
        "partial_fills": 0.5 * gross * 2 * spread_bp / 1e4,
    }
    return {k: round(v / nav, 5) for k, v in losses.items()} if nav else losses


def worst(state: Dict) -> float:
    r = run_all(state)
    return max(r.values()) if r else 0.0
