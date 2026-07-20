"""
src/alpha20/tournament/reconciliation.py — réconciliation PAR RUNNER.

Différence assumée avec le gate forward R0 (src.alpha20.validation.
live_reconciliation, qui compare Mongo ↔ ledger pour le paper 200k V1.1/
adaptatif) : les runners du tournoi n'ÉCRIVENT PAS dans Mongo — leur ledger
append-only EST l'unique source de vérité, par construction (pas de second
système à faire diverger). La réconciliation devient donc un contrôle
d'AUTO-COHÉRENCE, PAR INTERVALLE entre deux `mark` consécutifs : la NAV
RECALCULÉE depuis zéro (net_nav.nav, resommant tous les flux du ledger)
doit égaler la NAV ENREGISTRÉE au moment du mark (calculée indépendamment,
au fil de l'eau) à ≤ 0,01 USDT. Une divergence signale un bug de calcul de
NAV, pas une désynchronisation entre deux bases — c'est strictement ce que
« réconciliation Mongo/ledger ≤ 0,01 USDT par intervalle et par runner »
vise à empêcher : compter un fait deux fois ou l'oublier.

Un ledger invalide (chaîne rompue ou doublon divergent) rend le runner
INELIGIBLE — appliqué ici ET par le protocole de sélection (phase C).
"""
from __future__ import annotations

from typing import Dict, List

from src.alpha20.accounting import event_ledger, net_nav

GATE_USDT = 0.01


def runner_gate(runner_id: str, capital_eur: float) -> Dict:
    ledger_dir = event_ledger.runner_ledger_dir(runner_id)
    integ = event_ledger.integrity(ledger_dir)
    if not integ["chain_ok"]:
        return {"runner_id": runner_id, "status": "invalid_ledger",
                "passed": False, "consecutive_ok": 0, "integrity": integ,
                "eligible": False}
    marks = event_ledger.read(kinds=["mark"], ledger_dir=ledger_dir)
    if len(marks) < 2:
        return {"runner_id": runner_id, "status": "pending", "passed": None,
                "consecutive_ok": 0, "integrity": integ,
                "eligible": integ["one_event_per_fact"],
                "note": f"{len(marks)} mark(s) — gate évaluable à 2"}
    marks = marks.sort_values("ts").reset_index(drop=True)
    intervals: List[dict] = []
    for i in range(1, len(marks)):
        row = marks.iloc[i]
        recomputed = net_nav.nav(capital_eur, until=row["ts"], ledger_dir=ledger_dir)
        recorded = float((row["meta"] or {}).get("nav_usdt", float("nan")))
        gap = abs(recomputed - recorded)
        intervals.append({"ts": str(row["ts"]), "gap_usdt": round(gap, 6),
                          "passed": bool(gap <= GATE_USDT)})
    consecutive = 0
    for it in reversed(intervals):
        if not it["passed"]:
            break
        consecutive += 1
    all_ok = all(it["passed"] for it in intervals) and integ["one_event_per_fact"]
    return {"runner_id": runner_id, "status": "evaluated", "gate_usdt": GATE_USDT,
            "n_marks": int(len(marks)), "intervals": intervals[-30:],
            "consecutive_ok": consecutive if integ["one_event_per_fact"] else 0,
            "integrity": integ, "passed": bool(all_ok), "eligible": bool(all_ok)}


def all_runners_gate(specs) -> Dict[str, Dict]:
    """`specs` : Iterable[RunnerSpec] (typiquement runnable_specs())."""
    out = {}
    for spec in specs:
        out[spec.runner_id] = runner_gate(spec.runner_id, spec.capital_standalone_eur)
    return out
