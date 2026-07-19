"""
src/alpha20/portfolio/capacity_model.py — capacité réelle par sleeve (étape 3).

Deux estimateurs, le plus contraignant gagne :
  • participation : cap % du volume quotidien (défaut 1 % de l'ADV) ;
  • profondeur L2 : notional absorbable dans ±X bps × facteur de sécurité,
    multiplié par le nombre de rotations/jour que la stratégie tolère.

Les entrées viennent des collecteurs (l2 20 s Hyperliquid, klines Binance) —
aucune valeur magique cachée : tout paramètre est explicite.
"""
from __future__ import annotations

from typing import Optional


def capacity_participation(adv_usdt: float, participation: float = 0.01) -> float:
    return adv_usdt * participation


def capacity_l2(depth_usdt_within_bps: float, safety: float = 0.25,
                rotations_per_day: float = 1.0) -> float:
    return depth_usdt_within_bps * safety * rotations_per_day


def capacity_eur(adv_usdt: Optional[float] = None,
                 depth_usdt_within_bps: Optional[float] = None,
                 fx_usdt_eur: float = 1.0, **kw) -> float:
    caps = []
    if adv_usdt is not None:
        caps.append(capacity_participation(
            adv_usdt, kw.get("participation", 0.01)))
    if depth_usdt_within_bps is not None:
        caps.append(capacity_l2(depth_usdt_within_bps,
                                kw.get("safety", 0.25),
                                kw.get("rotations_per_day", 1.0)))
    if not caps:
        raise ValueError("capacité : fournir adv_usdt et/ou depth_usdt_within_bps")
    return min(caps) / fx_usdt_eur
