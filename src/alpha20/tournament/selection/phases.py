"""
src/alpha20/tournament/selection/phases.py — moteur d'états du protocole FIGÉ
(configs/alpha20_selection_protocol.yaml), étape 7.

Statuts : INELIGIBLE, OBSERVING, FRAGILE, ELIGIBLE, SELECTED_PROVISIONAL,
SELECTED_CONFIRMED, REJECTED, OBSERVE_ONLY. Verdict global possible :
NO_SELECTION.

Règle de priorité (aucune ambiguïté entre les statuts) :
  0. status registre (configs/alpha20_runners.yaml) ≠ ACTIVE (i.e.
     OBSERVE_ONLY) → OBSERVE_ONLY, inconditionnel, avant tout calcul de
     performance. Exclusion de gouvernance pure : le runner continue de
     s'exécuter et de produire de la télémétrie (runner_registry.runnable
     inclut OBSERVE_ONLY), mais n'entre JAMAIS dans eligible_ids/le
     classement/le clustering, quelle que soit sa performance ;
  1. ledger invalide / smoke KO / qualité de données insuffisante → INELIGIBLE
     (structurel, prime sur tout le reste, à tout moment) ;
  2. avant les seuils de la phase B (durée + décisions) → OBSERVING ;
  3. à la phase C : une règle de rejet dur déclenchée → REJECTED ;
  4. sinon, robustesse faible (bootstrap ≤ 0, retrait top-10 négatif) →
     FRAGILE ;
  5. sinon → ELIGIBLE (entre dans le classement/clustering) ;
  6. sélectionné (dominant de son cluster, dans l'ordre de classement) →
     SELECTED_PROVISIONAL ;
  7. confirmé après la fenêtre indépendante de la phase D →
     SELECTED_CONFIRMED (ou REJECTED si la confirmation échoue).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from src.alpha20.tournament import reconciliation
from src.alpha20.tournament.paper_account import PaperAccount
from src.alpha20.tournament.runner_registry import RunnerSpec
from src.alpha20.tournament.selection.manifest import (
    load_protocol, min_decisions_for, protocol_hash)
from src.alpha20.validation.bootstrap import (
    block_bootstrap_lcb95, bootstrap_lcb95, drop_top_n_events_return)
from src.alpha20.validation.promotion_gate import deflated_sharpe_ratio


def smoke_check(spec: RunnerSpec, account: PaperAccount) -> Dict:
    proto = load_protocol()["phase_a_smoke"]
    gate = reconciliation.runner_gate(spec.runner_id, spec.capital_standalone_eur)
    ledger_ok = gate["integrity"]["chain_ok"] and gate["integrity"]["one_event_per_fact"]
    risk_ok = account.drawdown() <= 0.10          # aucune violation flagrante précoce
    hours = account.age_days() * 24
    restart_ok = account.n_events() > 0            # le compte a survécu à ≥1 cycle
    data_ok = account.n_events() > 0
    checks = {"data_ok": data_ok, "risk_ok": risk_ok, "restart_ok": restart_ok,
             "ledger_ok": ledger_ok}
    passed_age = hours >= proto["min_hours"]
    return {"age_hours": round(hours, 2), "min_hours": proto["min_hours"],
            "checks": checks, "all_checks_ok": all(checks.values()),
            "passed": bool(passed_age and all(checks.values()))}


def observation_status(spec: RunnerSpec, account: PaperAccount) -> Dict:
    proto = load_protocol()["phase_b_observation"]
    min_dec = min_decisions_for(spec.runner_id)
    n_dec = int(len(account.read(kinds=["decision"])))
    age_days = account.age_days()
    ready = age_days >= proto["min_days"] and n_dec >= min_dec
    return {"age_days": round(age_days, 2), "min_days": proto["min_days"],
            "n_decisions": n_dec, "min_decisions": min_dec,
            "ready_for_selection": bool(ready),
            "leaderboard_informative_only": True}


def _hard_rejects(spec: RunnerSpec, account: PaperAccount, gate: Dict,
                  ret: pd.Series) -> List[str]:
    proto = load_protocol()["phase_c_selection"]["thresholds"]
    reasons = []
    if not gate["integrity"]["chain_ok"]:
        reasons.append("invalid_ledger")
    if account.drawdown() > proto["max_drawdown"]:
        reasons.append("max_drawdown_exceeded")
    es99 = account.es99_1d()
    if es99 is not None and es99 > proto["max_es99"]:
        reasons.append("max_es99_exceeded")
    if len(ret) and float(ret.sum()) < 0:
        reasons.append("net_return_negative")
    return reasons


def selection_status(spec: RunnerSpec) -> Dict:
    if spec.status != "ACTIVE":
        return {"runner_id": spec.runner_id, "status": "OBSERVE_ONLY",
                "reasons": ["registry_status_not_active"], "phase": "registry"}
    account = PaperAccount(spec.runner_id, spec.capital_standalone_eur)
    # Un ledger invalide passe AVANT tout — inconditionnel, sans gate d'âge.
    # Sinon une corruption qui vide `read()` (un fait manquant EST le
    # symptôme) fait paraître le compte "jeune" et masque le problème
    # qu'elle est censée signaler (régression trouvée par test).
    gate = reconciliation.runner_gate(spec.runner_id, spec.capital_standalone_eur)
    if not gate["integrity"]["chain_ok"] or not gate["integrity"]["one_event_per_fact"]:
        return {"runner_id": spec.runner_id, "status": "INELIGIBLE",
                "reasons": ["invalid_ledger"], "phase": "ledger_integrity"}

    smoke = smoke_check(spec, account)
    if not smoke["passed"] and smoke["age_hours"] >= smoke["min_hours"]:
        return {"runner_id": spec.runner_id, "status": "INELIGIBLE",
                "reasons": [k for k, v in smoke["checks"].items() if not v],
                "phase": "smoke"}
    obs = observation_status(spec, account)
    if not obs["ready_for_selection"]:
        return {"runner_id": spec.runner_id, "status": "OBSERVING",
                "reasons": [], "phase": "observation", "observation": obs}

    ret = account.daily_returns()
    hard = _hard_rejects(spec, account, gate, ret)
    if hard:
        return {"runner_id": spec.runner_id, "status": "REJECTED",
                "reasons": hard, "phase": "selection"}

    fills = account.read(kinds=["fill"])
    nav0 = spec.capital_standalone_eur
    lcb = bootstrap_lcb95(ret)
    block_lcb = block_bootstrap_lcb95(ret)
    top10 = drop_top_n_events_return(fills["amount_usdt"] if len(fills) else
                                     pd.Series(dtype=float), 10, nav0)
    robust = {"bootstrap_lcb95": lcb, "block_bootstrap_lcb95": block_lcb,
             "return_ex_top10_events": top10}
    fragile_reasons = [k for k, v in robust.items() if v is not None and v <= 0]
    if fragile_reasons:
        return {"runner_id": spec.runner_id, "status": "FRAGILE",
                "reasons": fragile_reasons, "phase": "selection",
                "robustness": robust}

    dsr = None
    if len(ret) >= 30:
        dsr = deflated_sharpe_ratio(ret, n_trials=1)   # corrigé au niveau tournoi ailleurs
    return {"runner_id": spec.runner_id, "status": "ELIGIBLE", "reasons": [],
            "phase": "selection", "robustness": robust,
            "bootstrap_lcb95": lcb, "dsr": round(dsr, 4) if dsr is not None else None,
            "return_total": round(float(ret.sum()), 5) if len(ret) else None}


def run_selection(specs: List[RunnerSpec]) -> Dict:
    """Phase C complète : statut par runner + verdict global (NO_SELECTION si
    aucun ELIGIBLE)."""
    from src.alpha20.tournament.selection.clustering import select_dominants

    statuses = {s.runner_id: selection_status(s) for s in specs}
    eligible_ids = [rid for rid, st in statuses.items() if st["status"] == "ELIGIBLE"]
    if not eligible_ids:
        return {"verdict": "NO_SELECTION", "statuses": statuses,
                "protocol_hash": protocol_hash()}
    eligible_specs = [s for s in specs if s.runner_id in eligible_ids]
    dominants, clusters = select_dominants(eligible_specs, statuses)
    for rid in dominants:
        statuses[rid] = dict(statuses[rid], status="SELECTED_PROVISIONAL")
    return {"verdict": "SELECTED_PROVISIONAL" if dominants else "NO_SELECTION",
            "statuses": statuses, "clusters": clusters,
            "selected": dominants, "protocol_hash": protocol_hash()}


def confirm(spec: RunnerSpec, provisional_config_hash: str,
           provisional_ts: str) -> Dict:
    """Phase D : fenêtre forward INDÉPENDANTE ≥14j / ≥100 événements NOUVEAUX,
    config INCHANGÉE depuis la sélection provisoire, aucun recalibrage."""
    proto = load_protocol()["phase_d_confirmation"]
    account = PaperAccount(spec.runner_id, spec.capital_standalone_eur)
    if spec.config_hash != provisional_config_hash:
        return {"runner_id": spec.runner_id, "status": "REJECTED",
                "reasons": ["config_changed_during_observation"], "phase": "confirmation"}
    df = account.read()
    new = df[df["ts"] > provisional_ts] if len(df) else df
    days = ((pd.Timestamp.now(tz="UTC") - pd.Timestamp(provisional_ts))
            .total_seconds() / 86400.0)
    if days < proto["min_days"] or len(new) < proto["min_new_events"]:
        return {"runner_id": spec.runner_id, "status": "SELECTED_PROVISIONAL",
                "reasons": [], "phase": "confirmation",
                "days_elapsed": round(days, 2), "new_events": int(len(new)),
                "required_days": proto["min_days"],
                "required_events": proto["min_new_events"]}
    st = selection_status(spec)
    if st["status"] not in ("ELIGIBLE",):
        return {"runner_id": spec.runner_id, "status": "REJECTED",
                "reasons": st.get("reasons", ["confirmation_window_failed"]),
                "phase": "confirmation", "days_elapsed": round(days, 2),
                "new_events": int(len(new))}
    return {"runner_id": spec.runner_id, "status": "SELECTED_CONFIRMED",
            "reasons": [], "phase": "confirmation",
            "days_elapsed": round(days, 2), "new_events": int(len(new))}
