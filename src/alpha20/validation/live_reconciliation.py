"""
src/alpha20/validation/live_reconciliation.py — rejouer + réconcilier (étape 1).

Gate d'entrée d'ALPHA_20 : le paper existant doit se réconcilier à ≤ 0,01 USDT
par volet ÉVÉNEMENTIEL (carry vs funding API, fees vs barème ligne à ligne).
Procédure :

  1. re-exécute l'audit indépendant (scripts/audit_paper_ledger.py — lecture
     seule, recalcul depuis les événements + API funding réelle) ;
  2. parse les résidus ; gate STRICT alpha20 : |gap| ≤ 0,01 USDT sur les
     volets événementiels (les volets en EUR arrondis au point d'historique
     gardent leur tolérance d'affichage propre) ;
  3. ingère les faits économiques audités (accruals de funding réels, lignes
     de frais) comme événements du ledger alpha20 — à partir d'ici la vérité
     comptable vit dans le ledger append-only, plus dans les agrégats Mongo ;
  4. écrit l'événement `reconciliation` (résidus + verdict + réf audit).

Usage : python -m src.alpha20.validation.live_reconciliation
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict

from src.alpha20 import ROOT
from src.alpha20.accounting import event_ledger
from src.alpha20.contracts import LedgerEvent

GATE_USDT = 0.01
EVENT_CHECKS = ("carry_vs_funding_api_usdt", "fees_vs_bareme_usdt")


def run_audit() -> Path:
    """Ré-exécute l'audit et retourne le chemin du JSON produit."""
    subprocess.run([sys.executable, str(ROOT / "scripts/audit_paper_ledger.py")],
                   check=True, capture_output=True, timeout=600)
    return sorted((ROOT / "reports" / "paper_audit").glob("AUDIT_*.json"))[-1]


def evaluate(audit: Dict) -> Dict:
    residuals = {k: abs(float(audit["checks"][k].get("gap", 0.0)))
                 for k in EVENT_CHECKS if k in audit["checks"]}
    passed = (bool(residuals)
              and all(v <= GATE_USDT for v in residuals.values())
              and all(c.get("ok") for c in audit["checks"].values()))
    return {"gate_usdt": GATE_USDT, "residuals_usdt": residuals,
            "audit_verdict": audit.get("verdict"), "passed": passed}


def ingest_facts(audit: Dict, audit_ref: str) -> int:
    """Faits audités → ledger (idempotent par event_id déterministe)."""
    events = []
    for row in audit.get("carry_detail", []):
        events.append(LedgerEvent(
            ts=str(row["funding_time"]), kind="funding",
            sleeve=f"carry_{row['symbol']}", venue="binance_usdm",
            amount_usdt=float(row["accrual_usdt"]),
            ref=audit_ref, meta={"rate": row["rate"],
                                 "notional_usdt": row["notional_usdt"]}))
    for row in audit.get("fees_lines", []):
        ts = str(row["line"]).split(" ")[0]
        events.append(LedgerEvent(
            ts=ts, kind="fee", sleeve="portfolio", venue="binance_usdm",
            amount_usdt=-abs(float(row["fee_usdt"])),
            ref=audit_ref, meta={"line": row["line"]}))
    return len(event_ledger.append(events))


def reconcile(run_fresh: bool = True) -> Dict:
    if run_fresh:
        path = run_audit()
    else:
        path = sorted((ROOT / "reports" / "paper_audit").glob("AUDIT_*.json"))[-1]
    audit = json.loads(path.read_text())
    result = evaluate(audit)
    n = ingest_facts(audit, audit_ref=path.name)
    event_ledger.append([LedgerEvent(
        ts=str(audit.get("run")), kind="reconciliation", sleeve="portfolio",
        venue="offchain", amount_usdt=0.0, ref=path.name, meta=result)])
    result.update({"audit_file": path.name, "facts_ingested": n,
                   "chain_ok": event_ledger.verify_chain()})
    return result


if __name__ == "__main__":
    r = reconcile(run_fresh="--cached" not in sys.argv)
    print(json.dumps(r, indent=1))
    sys.exit(0 if r["passed"] else 1)
