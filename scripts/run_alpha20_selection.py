#!/usr/bin/env python3
"""
scripts/run_alpha20_selection.py — moteur de sélection ALPHA_20 (étape 7).

Chaque exécution (timer quotidien) :
  1. calcule le statut de CHAQUE runner (smoke → observation → sélection) ;
  2. si des ELIGIBLE existent, sélectionne les dominants par cluster
     (SELECTED_PROVISIONAL) ; sinon publie NO_SELECTION ;
  3. pour tout runner déjà SELECTED_PROVISIONAL, vérifie la fenêtre de
     confirmation (phase D, ≥14j/≥100 événements NEUFS, config INCHANGÉE) —
     promeut en SELECTED_CONFIRMED ou rejette si la fenêtre échoue.

État persistant : reports/alpha20/tournament/SELECTION_STATE.json (append de
l'historique des verdicts, jamais écrasé sans trace — `history` grandit).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alpha20.guard import assert_paper_only                         # noqa: E402
from src.alpha20.tournament.runner_registry import runnable_specs        # noqa: E402
from src.alpha20.tournament.selection.manifest import protocol_hash      # noqa: E402
from src.alpha20.tournament.selection.phases import confirm, run_selection  # noqa: E402

STATE_PATH = ROOT / "reports" / "alpha20" / "tournament" / "SELECTION_STATE.json"


def _load() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {
        "history": [], "provisional": {}}


def main() -> None:
    assert_paper_only()
    specs = runnable_specs()
    by_id = {s.runner_id: s for s in specs}
    state = _load()
    now = datetime.now(timezone.utc).isoformat()

    result = run_selection(specs)

    # phase D : confirmer/rejeter tout SELECTED_PROVISIONAL déjà enregistré
    for rid, prov in list(state["provisional"].items()):
        if rid not in by_id:
            continue
        conf = confirm(by_id[rid], prov["config_hash"], prov["selected_at"])
        if conf["status"] in ("SELECTED_CONFIRMED", "REJECTED"):
            result["statuses"][rid] = conf
            state["provisional"].pop(rid)
        else:
            result["statuses"][rid] = conf   # toujours SELECTED_PROVISIONAL, motif d'attente

    # nouvelles sélections provisoires de ce cycle → enregistrées pour la phase D
    for rid in result.get("selected", []):
        if rid not in state["provisional"] and result["statuses"][rid]["status"] \
                == "SELECTED_PROVISIONAL":
            state["provisional"][rid] = {"config_hash": by_id[rid].config_hash,
                                         "selected_at": now}

    state["history"].append({"ts": now, "verdict": result["verdict"],
                             "protocol_hash": protocol_hash(),
                             "statuses": {k: v["status"]
                                         for k, v in result["statuses"].items()}})
    state["latest"] = {"ts": now, **result}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))

    print(f"[alpha20 selection] verdict={result['verdict']}")
    for rid, st in result["statuses"].items():
        print(f"  {rid}: {st['status']}"
              + (f" ({', '.join(st['reasons'])})" if st.get("reasons") else ""))
    print(f"-> {STATE_PATH}")


if __name__ == "__main__":
    main()
