#!/usr/bin/env python3
"""scripts/run_alpha20_tournament_reconcile.py — réconciliation PAR RUNNER
(auto-cohérence NAV recalculée ≡ NAV enregistrée, ≤0,01 USDT/intervalle).
Voir src.alpha20.tournament.reconciliation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alpha20.guard import assert_paper_only
from src.alpha20.tournament.reconciliation import all_runners_gate
from src.alpha20.tournament.runner_registry import runnable_specs

OUT = ROOT / "reports" / "alpha20" / "tournament" / "reconciliation_state.json"

if __name__ == "__main__":
    assert_paper_only()
    specs = runnable_specs()
    gates = all_runners_gate(specs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(gates, indent=2, default=str))
    for rid, g in gates.items():
        print(f"{rid}: {g['status']} passed={g['passed']} "
              f"consecutive_ok={g['consecutive_ok']} eligible={g.get('eligible')}")
    print(f"-> {OUT}")
