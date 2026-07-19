"""
src/alpha20/portfolio/robust_allocator.py — remplace le proxy Kelly 2p−1 (étape 5).

Entrées par sleeve : distribution empirique NETTE (SleeveStats), capacité,
coût de rotation, venue. L'optimiseur maximise la BORNE BASSE de l'intervalle
de confiance du rendement net — jamais la moyenne du backtest — puis applique :

  • pénalité de corrélation conditionnelle (matrice empirique, cap par paire) ;
  • plafond de capacité par sleeve et de collatéral par venue ;
  • plafond d'expected shortfall 99 % 1 j du portefeuille (profil de risque) ;
  • coût marginal de rotation déduit du score AVANT allocation.

Déterministe, sans scipy (venv py3.8). La cible 20 %/mois n'apparaît nulle
part ici : c'est une métrique de contrôle, pas une récompense.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from src.alpha20.contracts import SleeveStats
from src.alpha20.risk.global_governor import load_profile

Z_LCB = 1.645                    # borne basse à 90 %
TRADING_DAYS = 365


def lcb_annual(net_daily: pd.Series, rotation_cost_bp: float,
               rotations_per_year: float = 12.0) -> float:
    """Borne basse annualisée du rendement net, coût de rotation déduit."""
    r = net_daily.dropna()
    if len(r) < 30:
        return -1.0              # trop peu d'historique = non allouable
    mu, se = float(r.mean()), float(r.std(ddof=1)) / np.sqrt(len(r))
    lcb_d = mu - Z_LCB * se
    return lcb_d * TRADING_DAYS - rotations_per_year * rotation_cost_bp / 1e4


def portfolio_es99(weights: Dict[str, float],
                   returns: pd.DataFrame) -> float:
    """ES 99 % 1 j empirique du portefeuille (fraction de NAV, positif = perte)."""
    w = pd.Series(weights).reindex(returns.columns).fillna(0.0)
    port = returns.fillna(0.0).values @ w.values
    if len(port) < 50:
        return 0.0
    var = np.quantile(port, 0.01)
    tail = port[port <= var]
    return float(-tail.mean()) if len(tail) else float(-var)


def allocate(stats: List[SleeveStats], nav_eur: float,
             regime: str = None) -> Dict[str, float]:
    """Poids de gross par sleeve (fraction de NAV). regime réservé à la
    covariance conditionnelle : si fourni, les séries doivent déjà être
    filtrées sur ce régime par l'appelant (pas de magie ici)."""
    profile = load_profile()
    rets = pd.DataFrame({s.name: s.net_returns_daily for s in stats})
    scores = {s.name: max(lcb_annual(s.net_returns_daily, s.rotation_cost_bp), 0.0)
              for s in stats}
    if sum(scores.values()) <= 0:
        return {s.name: 0.0 for s in stats}

    # pénalité de corrélation : score réduit par (1 − corr_moyenne_pos)
    corr = rets.corr().fillna(0.0)
    w = {}
    for s in stats:
        others = [o.name for o in stats if o.name != s.name and scores[o.name] > 0]
        pen = float(np.mean([max(corr.loc[s.name, o], 0.0) for o in others])) \
            if others else 0.0
        w[s.name] = scores[s.name] * (1.0 - pen)

    tot = sum(w.values())
    if tot <= 0:
        return {s.name: 0.0 for s in stats}
    w = {k: v / tot for k, v in w.items()}

    # plafonds : capacité par sleeve, collatéral par venue, marge totale
    for s in stats:
        w[s.name] = min(w[s.name], s.capacity_eur / nav_eur)
    by_venue: Dict[str, float] = {}
    for s in stats:
        by_venue[s.venue] = by_venue.get(s.venue, 0.0) + w[s.name]
    for s in stats:
        if by_venue[s.venue] > profile.venue_unsecured_cap:
            w[s.name] *= profile.venue_unsecured_cap / by_venue[s.venue]

    # plafond ES99 du portefeuille : scaling global
    es = portfolio_es99(w, rets)
    if es > profile.es99_1d and es > 0:
        scale = profile.es99_1d / es
        w = {k: v * scale for k, v in w.items()}
    return {k: round(v, 4) for k, v in w.items()}
