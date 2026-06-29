"""
src/institutional/portfolio/asset_edge_gate.py
─────────────────────────────────────────────────────────────────────────────
Gate par-actif fondé sur l'EDGE RÉALISÉ NET DE FRAIS (causal, walk-forward).

Problème mesuré (PARALLEL_50) : élargir l'univers capte plus de directionnel brut
mais les FRAIS le mangent — les signaux pullback de beaucoup d'alts ne couvrent pas
leurs coûts de transaction. Un filtre fondé sur le `expected_return` du modèle ne suffit
pas : il fait confiance à un P(up) que le modèle SUR-ESTIME sur les alts, et il ignore
le downside.

Solution honnête : on ne fait confiance qu'au RÉALISÉ. Pour l'année Y, un actif n'est
tradable que si, sur les années < Y (donnée passée uniquement → aucun lookahead), ses
signaux A_TRADE ont rapporté en moyenne un PnL net de frais positif, sur un échantillon
suffisant. Les alts dont l'edge ne couvre pas les frais sont écartés AVANT d'être tradés.

Approximation assumée (screen) : edge réalisé d'un signal = rendement forward à l'horizon
du moteur, moins un aller-retour de frais. Les majors (BTC/ETH) sont exemptés (cœur de book
déjà prouvé) ; le gate ne filtre que l'expansion.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd

MAJORS = ("BTCUSDT", "ETHUSDT")


class AssetEdgeGate:
    """Décide, par (actif, année), si l'actif a prouvé un edge net positif AVANT cette année."""

    def __init__(self, min_net: float = 0.0, min_signals: int = 20,
                 exempt_majors: bool = True):
        self.min_net = min_net
        self.min_signals = min_signals
        self.exempt_majors = exempt_majors
        self._allow: Dict[Tuple[str, int], bool] = {}
        self._stats: Dict[Tuple[str, int], dict] = {}   # diagnostic

    def fit(self, opps: List, prices: Dict[str, pd.Series], roundtrip_cost: float) -> "AssetEdgeGate":
        # 1. edge réalisé net par signal, groupé par (actif, année du signal)
        by_ay: Dict[Tuple[str, int], List[float]] = defaultdict(list)
        for o in opps:
            a = o.asset
            s = prices.get(a)
            if s is None:
                continue
            ts = pd.Timestamp(o.timestamp)
            i = s.index.searchsorted(ts, side="right") - 1
            if i < 0:
                continue
            h = max(1, int(round(float(o.expected_holding_hours))))
            j = min(i + h, len(s) - 1)
            if j <= i:
                continue
            fwd = float(s.iloc[j]) / float(s.iloc[i]) - 1.0
            by_ay[(a, ts.year)].append(fwd - roundtrip_cost)   # net de frais

        # 2. pour chaque actif : décision année Y fondée sur le cumul des années < Y
        assets = sorted({a for (a, _y) in by_ay})
        for a in assets:
            years = sorted({y for (aa, y) in by_ay if aa == a})
            if not years:
                continue
            prior: List[float] = []
            for y in range(min(years), max(years) + 2):   # +2 : autorise aussi l'année suivant la dernière
                n = len(prior)
                mean_net = (sum(prior) / n) if n else None
                allowed = (n >= self.min_signals and mean_net is not None and mean_net > self.min_net)
                self._allow[(a, y)] = allowed
                self._stats[(a, y)] = {"n_prior": n,
                                       "mean_net_prior": (round(mean_net, 5) if mean_net is not None else None),
                                       "allowed": allowed}
                prior.extend(by_ay.get((a, y), []))         # le réalisé de Y devient connu pour Y+1
        return self

    def allows(self, asset: str, ts) -> bool:
        if self.exempt_majors and asset in MAJORS:
            return True
        return self._allow.get((asset, pd.Timestamp(ts).year), False)

    def allowed_assets(self, year: int) -> List[str]:
        return sorted({a for (a, y), ok in self._allow.items() if y == year and ok})

    def summary(self) -> dict:
        return {f"{a}_{y}": st for (a, y), st in sorted(self._stats.items())}
