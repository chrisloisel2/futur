#!/usr/bin/env python3
"""
scripts/promote_engine.py
─────────────────────────────────────────────────────────────────────────────
Évalue la promotion live d'un moteur depuis le Decision Ledger (validation
bayésienne) et met à jour le registre de statuts. Ne promeut JAMAIS de paper à
full live directement.

Usage :
    python3 scripts/promote_engine.py --engine TRM_TREND_INST --current SHADOW
    python3 scripts/promote_engine.py --all
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.monitoring.decision_ledger import DecisionLedger
from src.institutional.evaluation.live_validation import evaluate_engine, profit_factor

logging.basicConfig(level=logging.WARNING)
REGISTRY = Path("artifacts/institutional/engines/status_registry.json")


def _load_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return {}


def _save_registry(reg: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def evaluate_one(df, engine_id: str, current: str) -> dict:
    g = df[df["engine_id"] == engine_id]
    a = g[g["decision_zone"] == "A_TRADE"]["realized_shadow_result"].dropna().to_numpy()
    b = g[g["decision_zone"] == "B_SHADOW"]["realized_shadow_result"].dropna().to_numpy()
    shadow_pf = profit_factor(b) if len(b) else None
    res = evaluate_engine(engine_id, a, current_status=current, drift=0.0, shadow_pf=shadow_pf)
    return res.to_dict()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=None)
    ap.add_argument("--current", default="SHADOW")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="écrit le statut recommandé dans le registre")
    args = ap.parse_args()

    ledger = DecisionLedger()
    df = ledger.load()
    if df.empty:
        print("Ledger vide — lancer backfill_decision_ledger.py.")
        return

    reg = _load_registry()
    engines = sorted(df["engine_id"].unique()) if args.all else [args.engine]
    print(f"\n{'Moteur':<22}{'n':>6}{'ESS':>7}{'PF':>7}{'P(PF>1.3)':>11}{'P(DD<3%)':>10}  {'cur→reco'}")
    print("─" * 86)
    for eid in engines:
        if eid is None:
            continue
        cur = reg.get(eid, {}).get("status", args.current)
        r = evaluate_one(df, eid, cur)
        print(f"{eid:<22}{r['n_trades']:>6}{r['ess']:>7.0f}{r['pf']:>7.2f}"
              f"{r['p_pf_gt_130']:>11.2f}{r['p_dd_lt_3']:>10.2f}  {cur}→{r['recommended_status']}")
        if args.apply:
            reg[eid] = {"status": r["recommended_status"], "evidence": r}
    if args.apply:
        _save_registry(reg)
        print(f"\n→ registre mis à jour: {REGISTRY}")
    print()


if __name__ == "__main__":
    main()
