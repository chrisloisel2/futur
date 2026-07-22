#!/usr/bin/env python3
"""
src/alpha20/tournament/portfolio_search.py — RECHERCHE DE PORTEFEUILLE (étape 8).

Opère sur les runners ELIGIBLE/SELECTED_* du tournoi, jamais sur des courbes
multipliées entre elles — tout passe par `joint_simulator.simulate` (capital
partagé, marge, capacité, fiscalité). Compare :

  • meilleur runner seul (poids 100% sur le dominant du classement) ;
  • allocation égale entre tous les éligibles ;
  • allocation contrainte par la borne basse (robust_allocator — LCB, pas la
    moyenne du backtest, plafonds capacité/venue/ES99) ;
  • portefeuille sans chaque runner, successivement (contribution marginale) ;
  • scénarios fiscaux PFU 30% et BNC prudent (45%, borne haute) ;
  • champion (sélection réelle) contre challengers (meilleurs ELIGIBLE non
    retenus par le clustering).

Historique insuffisant (< 30 points communs) → résultat `null`, jamais un
chiffre extrapolé.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import pandas as pd

from src.alpha20 import ROOT
from src.alpha20.contracts import CostSnapshot, SleeveStats
from src.alpha20.costs.fee_registry import effective_costs
from src.alpha20.portfolio import robust_allocator as ra
from src.alpha20.portfolio.joint_simulator import SleeveInput, simulate
from src.alpha20.tournament.paper_account import PaperAccount

MIN_COMMON_POINTS = 30
OUT = ROOT / "reports" / "alpha20" / "tournament" / "portfolio_search.json"


def _returns(spec) -> pd.Series:
    return PaperAccount(spec.runner_id, spec.capital_standalone_eur).daily_returns()


def _sleeve_inputs(specs, weights: Dict[str, float]) -> List[SleeveInput]:
    out = []
    for s in specs:
        r = _returns(s)
        costs = effective_costs(s.venue or "binance_usdm",
                                s.assets[0] if isinstance(s.assets, list) and s.assets
                                else "PERP")
        out.append(SleeveInput(name=s.runner_id, net_returns=r,
                               weight=weights.get(s.runner_id, 0.0), costs=costs,
                               venue=s.venue or "binance_usdm",
                               capacity_eur=s.capital_standalone_eur * 5))
    return out


def _simulate_or_none(specs, weights: Dict[str, float], capital_eur: float,
                      tax_scenario: Optional[str] = None) -> Optional[dict]:
    common = None
    for s in specs:
        r = _returns(s)
        idx = set(r.index)
        common = idx if common is None else common & idx
    if not common or len(common) < MIN_COMMON_POINTS:
        return None
    sleeves = _sleeve_inputs(specs, weights)
    res = simulate(sleeves, capital_eur, borrow_ann=0.08, tax_scenario=tax_scenario)
    return res.summary


def search(specs, statuses: Dict[str, dict], selected_ids: List[str],
          capital_eur: float = 200000.0) -> Dict:
    if not specs:
        return {"status": "no_eligible_runners"}

    def rank_key(rid):
        st = statuses.get(rid, {})
        return st.get("bootstrap_lcb95") or st.get("return_total") or float("-inf")

    ranked = sorted([s.runner_id for s in specs], key=rank_key, reverse=True)
    best_single = ranked[0]

    results: Dict[str, Optional[dict]] = {}
    results["best_single"] = _simulate_or_none(
        specs, {best_single: 1.0}, capital_eur)

    equal_w = {s.runner_id: 1.0 / len(specs) for s in specs}
    results["equal_weight"] = _simulate_or_none(specs, equal_w, capital_eur)

    stats = [SleeveStats(s.runner_id, _returns(s), s.capital_standalone_eur * 5,
                         s.venue or "binance_usdm", rotation_cost_bp=8.0)
             for s in specs]
    lcb_w = ra.allocate(stats, capital_eur)
    results["lcb_constrained"] = _simulate_or_none(specs, lcb_w, capital_eur)

    loo = {}
    for excl in specs:
        remaining = [s for s in specs if s.runner_id != excl.runner_id]
        if not remaining:
            loo[excl.runner_id] = None
            continue
        w = {s.runner_id: 1.0 / len(remaining) for s in remaining}
        r = _simulate_or_none(remaining, w, capital_eur)
        base = results["equal_weight"]
        loo[excl.runner_id] = {
            "without_runner": r,
            "marginal_contribution_ann": round(
                base["net_return_ann"] - r["net_return_ann"], 5)
            if base and r else None}
    results["leave_one_out"] = loo

    results["tax_scenarios"] = {
        "pfu_30": _simulate_or_none(specs, equal_w, capital_eur, "pfu_30"),
        "bnc_prudent_45": _simulate_or_none(specs, equal_w, capital_eur, "bnc_45"),
    }

    challengers = [rid for rid in ranked if rid not in selected_ids][:3]
    results["champion_vs_challengers"] = {
        "champion": {"runner_ids": selected_ids,
                    "result": _simulate_or_none(
                        [s for s in specs if s.runner_id in selected_ids],
                        {rid: 1.0 / max(len(selected_ids), 1) for rid in selected_ids},
                        capital_eur) if selected_ids else None},
        "challengers": {rid: results["best_single"] if rid == best_single else
                        _simulate_or_none(
                            [s for s in specs if s.runner_id == rid], {rid: 1.0},
                            capital_eur)
                        for rid in challengers},
    }
    return {"status": "ok", "ranked": ranked, "results": results}


if __name__ == "__main__":
    from src.alpha20.deployment_guard import assert_deployment_matches_approved
    from src.alpha20.guard import assert_paper_only
    from src.alpha20.tournament.runner_registry import runnable_specs
    from src.alpha20.tournament.selection.phases import run_selection

    assert_paper_only()
    assert_deployment_matches_approved()
    specs = runnable_specs()
    sel = run_selection(specs)
    eligible = [s for s in specs if sel["statuses"].get(s.runner_id, {}).get("status")
               in ("ELIGIBLE", "SELECTED_PROVISIONAL", "SELECTED_CONFIRMED")]
    out = search(eligible, sel["statuses"], sel.get("selected", []))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"status": out["status"],
                      "n_eligible": len(eligible)}, indent=2))
    print(f"-> {OUT}")
