#!/usr/bin/env python3
"""
src/alpha20/tournament/orchestrator.py — CYCLE du tournoi ALPHA_20.

Un cycle :
  1. garde structurelle (aucun ordre réel possible) ;
  2. charge les runners ACTIVE/OBSERVE_ONLY du registre versionné ;
  3. interroge CHAQUE adaptateur pour l'univers de données dont il pourrait
     avoir besoin ce cycle (`required_universe/funding/quarterly_pairs`),
     UNIT le tout, construit UN SEUL snapshot du bus — tous les runners du
     cycle voient exactement les mêmes observations au même cutoff ;
  4. pour chaque runner, dans un thread ISOLÉ avec timeout dur : calcule
     l'état de risque courant (governor unifié), appelle `decide()`, émet
     les événements dans le compte ISOLÉ du runner, persiste l'état
     opérationnel, marque la NAV. Une exception ou un dépassement de
     timeout n'affecte JAMAIS les autres runners (isolation totale) ;
  5. jamais de suppression/troncature/reset d'un ledger — uniquement des
     appends idempotents.

Aucun accès réseau direct hors du bus (construit une fois, section 3).
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.alpha20.deployment_guard import assert_deployment_matches_approved  # noqa: E402
from src.alpha20.guard import assert_paper_only                    # noqa: E402
from src.alpha20.execution.paper_broker import PaperBroker          # noqa: E402
from src.alpha20.tournament import market_bus                       # noqa: E402
from src.alpha20.tournament.paper_account import PaperAccount       # noqa: E402
from src.alpha20.tournament.runner_adapters import build_adapter    # noqa: E402
from src.alpha20.tournament.runner_registry import runnable_specs   # noqa: E402

STATE_DIR = ROOT / "data" / "alpha20" / "tournament" / "state"
CYCLE_LOG_DIR = ROOT / "data" / "alpha20" / "tournament" / "cycle_log"
RUNNER_TIMEOUT_S = 90


def _log_cycle(runner_id: str, status: str) -> None:
    """Télémétrie de DISPONIBILITÉ — opérationnelle, PAS un fait économique
    (donc hors du ledger append-only) : append JSONL simple."""
    CYCLE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "status": status}
    with open(CYCLE_LOG_DIR / f"{runner_id}.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")


def _state_path(runner_id: str) -> Path:
    return STATE_DIR / f"{runner_id}.json"


def load_state(runner_id: str) -> dict:
    p = _state_path(runner_id)
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(runner_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(runner_id).write_text(json.dumps(state, indent=2, default=str))


def _run_one(spec, snapshot, broker: PaperBroker) -> dict:
    account = PaperAccount(spec.runner_id, spec.capital_standalone_eur)
    state = load_state(spec.runner_id)
    # risque calculé sur l'état de la FIN du cycle précédent (gross/delta
    # persistés) — décide si CE cycle peut ouvrir du risque neuf
    gross = float(state.get("gross_usdt", 0.0))
    decision = account.evaluate_risk(
        gross_usdt=gross, net_delta_usdt=float(state.get("net_delta_usdt", 0.0)),
        venue_unsecured_frac={spec.venue or "n/a":
                              gross / max(account.nav_usdt(), 1.0)})
    adapter = build_adapter(spec)
    events, new_state = adapter.decide(snapshot, broker, state, decision.state)
    if decision.state != state.get("last_risk_state"):
        from src.alpha20.contracts import LedgerEvent
        events = events + [LedgerEvent(
            ts=datetime.now(timezone.utc).isoformat(), kind="kill",
            sleeve="account", venue="offchain", amount_usdt=0.0,
            ref="risk_transition",
            meta={"from": state.get("last_risk_state"), "to": decision.state,
                 "reasons": decision.reasons})]
    new_state["last_risk_state"] = decision.state
    account.emit(events)
    try:
        from src.institutional.live.paper_portfolio import btc_regime
        regime = btc_regime()
    except Exception:                             # noqa: BLE001
        regime = "UNKNOWN"
    account.mark(account.nav_usdt(), extra_meta={
        "risk_state": decision.state, "config_hash": spec.config_hash,
        "market_event_id": snapshot.market_event_id, "regime": regime})
    save_state(spec.runner_id, new_state)
    integ = account.integrity()
    return {"runner_id": spec.runner_id, "status": "ok",
            "n_events": len(events), "risk_state": decision.state,
            "nav_usdt": round(account.nav_usdt(), 2),
            "chain_ok": integ["chain_ok"],
            "one_event_per_fact": integ["one_event_per_fact"]}


def run_cycle() -> Dict[str, dict]:
    assert_paper_only()
    assert_deployment_matches_approved()
    specs = runnable_specs()
    if not specs:
        print("[tournament] aucun runner ACTIVE/OBSERVE_ONLY — rien à faire", flush=True)
        return {}

    universe, funding_syms, quarterly_pairs = set(), set(), set()
    adapters_by_id = {}
    states_by_id = {}
    for spec in specs:
        states_by_id[spec.runner_id] = load_state(spec.runner_id)
        try:
            a = build_adapter(spec)
        except KeyError:
            continue
        adapters_by_id[spec.runner_id] = a
        try:
            universe.update(a.required_universe(states_by_id[spec.runner_id]))
            funding_syms.update(a.required_funding(states_by_id[spec.runner_id]))
            quarterly_pairs.update(
                a.required_quarterly_pairs(states_by_id[spec.runner_id]))
        except Exception as e:                    # noqa: BLE001 — un peek raté n'annule pas le cycle
            print(f"[tournament] required_* échec pour {spec.runner_id}: {e}",
                  flush=True)

    snapshot = market_bus.build_snapshot(sorted(universe), sorted(funding_syms),
                                         sorted(quarterly_pairs))
    broker = PaperBroker()

    # Isolation RÉELLE : concurrent.futures.wait() rend la main après
    # RUNNER_TIMEOUT_S même si un thread est encore bloqué (Python ne peut
    # pas tuer un thread — un appel réseau interne a de toute façon son
    # propre timeout, comme partout ailleurs dans ce dépôt). shutdown(wait=
    # False) évite que CE cycle attende la fin du thread abandonné ; le
    # thread orphelin se termine seul à l'expiration de son appel réseau et
    # n'affecte pas le PROCESS suivant (chaque cycle systemd est un process
    # séparé) — c'est le niveau où "ne pas bloquer les autres" compte.
    results = {}
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=len(specs) or 1)
    futures = {ex.submit(_run_one, spec, snapshot, broker): spec for spec in specs}
    done, not_done = concurrent.futures.wait(futures, timeout=RUNNER_TIMEOUT_S)
    for fut in done:
        spec = futures[fut]
        try:
            results[spec.runner_id] = fut.result()
            _log_cycle(spec.runner_id, "ok")
        except Exception as e:                    # noqa: BLE001 — isolation totale
            results[spec.runner_id] = {"runner_id": spec.runner_id,
                                       "status": "error", "error": str(e)}
            _log_cycle(spec.runner_id, "error")
    for fut in not_done:
        spec = futures[fut]
        results[spec.runner_id] = {"runner_id": spec.runner_id, "status": "timeout"}
        _log_cycle(spec.runner_id, "timeout")
    ex.shutdown(wait=False)
    for rid, r in results.items():
        print(f"[tournament] {rid}: {r.get('status')} "
              f"events={r.get('n_events','-')} nav={r.get('nav_usdt','-')} "
              f"risk={r.get('risk_state','-')}", flush=True)
    print(f"[tournament] cycle terminé — market_event_id={snapshot.market_event_id} "
          f"gaps={snapshot.gaps}", flush=True)
    return results


if __name__ == "__main__":
    run_cycle()
