"""
src/alpha20/portfolio/joint_simulator.py — simulateur PORTEFEUILLE COMMUN (étape 3).

Le combiné V12_PLUS_STACK_OVERLAY_MH multipliait des courbes de sleeves comme
si chacune disposait de tout le capital : ici un SEUL bilan. À chaque pas :

  1. cibles de gross par sleeve = poids × equity, plafonnées par
     (a) la marge initiale totale ≤ margin_used_cap (profil de risque),
     (b) la capacité déclarée de chaque sleeve ;
  2. frais réels sur le turnover |Δgross| (CostSnapshot du sleeve — jamais
     de bps codés en dur ici) ;
  3. borrow couru sur le gross spot au-delà de l'equity ;
  4. PnL = Σ gross_i × r_i (r_i = rendement NET DE COÛTS INTERNES du sleeve
     sur son propre notional ; les coûts PARTAGÉS — borrow, frais de
     réallocation, fiscalité — sont comptés ici, une seule fois) ;
  5. panne de venue optionnelle : sleeves de la venue figés (r = 0, marge
     immobilisée, aucun exit) sur la fenêtre donnée ;
  6. provision fiscale mensuelle sur le net positif (tax_engine).

V0 assumé et documenté : pas encore de fills partiels ni de legs non
synchronisés intra-jour (étape 7 TCA les mesurera) ; grain quotidien.

Sortie : equity nette, décomposition, et `frontier()` →
rendement net / maxDD / capital immobilisé / utilisation de capacité.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.alpha20.accounting.tax_engine import provision_for_month
from src.alpha20.contracts import CostSnapshot
from src.alpha20.risk.global_governor import load_profile


@dataclass
class SleeveInput:
    name: str
    net_returns: pd.Series          # quotidien, net de coûts internes, sur SON notional
    weight: float                   # gross cible en fraction d'equity
    costs: CostSnapshot             # coûts de RÉALLOCATION (source obligatoire)
    venue: str = "binance_usdm"
    init_margin_frac: float = 0.10  # marge initiale requise / gross
    maint_margin_frac: float = 0.05
    capacity_eur: float = 1e9
    spot_leg_frac: float = 0.0      # part du gross en spot (génère du borrow si levé)


@dataclass
class SimResult:
    equity: pd.Series
    summary: Dict
    by_sleeve: Dict[str, float] = field(default_factory=dict)


def simulate(sleeves: List[SleeveInput], capital_eur: float,
             borrow_ann: float, tax_scenario: str = None,
             venue_outage: Optional[Dict] = None) -> SimResult:
    """venue_outage: {"venue": str, "start": ts, "days": int} (scénario)."""
    profile = load_profile()
    idx = sorted(set().union(*[s.net_returns.index for s in sleeves]))
    idx = pd.DatetimeIndex(idx)
    eq = capital_eur
    prev_gross = {s.name: 0.0 for s in sleeves}
    rows, fees_cum, borrow_cum, tax_cum = [], 0.0, 0.0, 0.0
    pnl_sleeve = {s.name: 0.0 for s in sleeves}
    month, month_net = None, 0.0
    out_start = out_end = None
    if venue_outage:
        out_start = pd.Timestamp(venue_outage["start"])
        out_end = out_start + pd.Timedelta(days=venue_outage.get("days", 1))

    for t in idx:
        # 1) cibles plafonnées par marge totale et capacité
        tgt = {s.name: s.weight * eq for s in sleeves}
        im = sum(tgt[s.name] * s.init_margin_frac for s in sleeves)
        cap_scale = min(1.0, (profile.margin_used_cap * eq) / im) if im > 0 else 1.0
        frozen = {s.name for s in sleeves
                  if out_start is not None and out_start <= t < out_end
                  and s.venue == venue_outage["venue"]}
        day_fee = day_borrow = day_pnl = 0.0
        margin_used = 0.0
        for s in sleeves:
            if s.name in frozen:            # venue morte : position figée
                g = prev_gross[s.name]
            else:
                g = min(tgt[s.name] * cap_scale, s.capacity_eur)
                turn = abs(g - prev_gross[s.name])
                day_fee += turn * (s.costs.taker_bp
                                   + (s.costs.slippage_bp or 0.0)) / 1e4
            r = float(s.net_returns.get(t, 0.0)) if s.name not in frozen else 0.0
            day_pnl += prev_gross[s.name] * r
            pnl_sleeve[s.name] += prev_gross[s.name] * r
            margin_used += g * s.init_margin_frac
            day_borrow += max(g * s.spot_leg_frac - eq / max(len(sleeves), 1), 0.0)
            prev_gross[s.name] = g
        day_borrow_cost = day_borrow * borrow_ann / 365.0
        net_day = day_pnl - day_fee - day_borrow_cost
        # 6) provision fiscale au changement de mois
        m = t.strftime("%Y-%m")
        if month is not None and m != month and month_net > 0:
            prov = provision_for_month(month_net, month, tax_scenario)["provision_usdt"]
            eq -= prov
            tax_cum += prov
            month_net = 0.0
        elif month is not None and m != month:
            month_net = 0.0
        month = m
        month_net += net_day
        eq += net_day
        fees_cum += day_fee
        borrow_cum += day_borrow_cost
        rows.append((t, eq, margin_used))

    eqs = pd.Series([r[1] for r in rows], index=[r[0] for r in rows])
    peak = eqs.cummax()
    maxdd = float(((eqs - peak) / peak).min()) if len(eqs) else 0.0
    margin_series = pd.Series([r[2] for r in rows], index=eqs.index)
    years = max(len(idx) / 365.0, 1e-9)
    cap_util = {s.name: round(s.weight * capital_eur / s.capacity_eur, 4)
                for s in sleeves}
    return SimResult(
        equity=eqs,
        summary={
            "net_return_total": float(eqs.iloc[-1] / capital_eur - 1) if len(eqs) else 0.0,
            "net_return_ann": float((eqs.iloc[-1] / capital_eur) ** (1 / years) - 1)
                              if len(eqs) else 0.0,
            "max_drawdown": maxdd,
            "capital_immobilise_avg": float(margin_series.mean()) if len(eqs) else 0.0,
            "capital_immobilise_max": float(margin_series.max()) if len(eqs) else 0.0,
            "fees_eur": round(fees_cum, 2), "borrow_eur": round(borrow_cum, 2),
            "tax_provision_eur": round(tax_cum, 2),
            "capacity_utilization": cap_util,
        },
        by_sleeve={k: round(v, 2) for k, v in pnl_sleeve.items()})


def frontier(sleeves: List[SleeveInput], capital_eur: float, borrow_ann: float,
             gross_grid: List[float]) -> pd.DataFrame:
    """Frontière OBLIGATOIRE : rendement net / maxDD / capital immobilisé /
    capacité, en balayant un multiplicateur de gross global."""
    rows = []
    for mult in gross_grid:
        scaled = [SleeveInput(**{**s.__dict__, "weight": s.weight * mult})
                  for s in sleeves]
        r = simulate(scaled, capital_eur, borrow_ann)
        rows.append({"gross_mult": mult,
                     "net_return_ann": r.summary["net_return_ann"],
                     "max_drawdown": r.summary["max_drawdown"],
                     "capital_immobilise_max": r.summary["capital_immobilise_max"],
                     "max_capacity_util": max(
                         r.summary["capacity_utilization"].values() or [0.0])})
    return pd.DataFrame(rows)
