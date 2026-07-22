#!/usr/bin/env python3
"""scripts/run_alpha20_portfolio_search.py — recherche de portefeuille (étape
8) sur les runners ELIGIBLE/SELECTED_*. Voir src.alpha20.tournament.portfolio_search."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alpha20.deployment_guard import assert_deployment_matches_approved
from src.alpha20.guard import assert_paper_only
from src.alpha20.tournament.portfolio_search import OUT, search
from src.alpha20.tournament.runner_registry import runnable_specs
from src.alpha20.tournament.selection.phases import run_selection

if __name__ == "__main__":
    assert_paper_only()
    assert_deployment_matches_approved()
    specs = runnable_specs()
    sel = run_selection(specs)
    eligible = [s for s in specs if sel["statuses"].get(s.runner_id, {}).get("status")
               in ("ELIGIBLE", "SELECTED_PROVISIONAL", "SELECTED_CONFIRMED")]
    out = search(eligible, sel["statuses"], sel.get("selected", []))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"status": out["status"], "n_eligible": len(eligible)}, indent=2))
    print(f"-> {OUT}")
