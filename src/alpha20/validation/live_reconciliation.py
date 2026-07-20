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
    """Faits audités → ledger (idempotent par event_id déterministe).
    Les faits datés de l'ÈRE SOURCE (≥ premier rebalance émis par le paper)
    sont refusés : ils existent déjà, les ré-ingérer doublerait la NAV."""
    dec = event_ledger.read(kinds=["decision"])
    dec = dec[dec["ref"] == "rebalance"] if len(dec) else dec
    source_era_start = str(dec["ts"].iloc[0]) if len(dec) else "9999"
    events = []
    for row in audit.get("carry_detail", []):
        if str(row["funding_time"]) >= source_era_start:
            continue
        events.append(LedgerEvent(
            ts=str(row["funding_time"]), kind="funding",
            sleeve=f"carry_{row['symbol']}", venue="binance_usdm",
            amount_usdt=float(row["accrual_usdt"]),
            ref=audit_ref, meta={"rate": row["rate"],
                                 "notional_usdt": row["notional_usdt"]}))
    for row in audit.get("fees_lines", []):
        ts = str(row["line"]).split(" ")[0]
        if ts >= source_era_start:
            continue
        events.append(LedgerEvent(
            ts=ts, kind="fee", sleeve="portfolio", venue="binance_usdm",
            amount_usdt=-abs(float(row["fee_usdt"])),
            ref=audit_ref, meta={"line": row["line"]}))
    return len(event_ledger.append(events))


def forward_gate() -> Dict:
    """Gate FORWARD (la seule vérité après le 2026-07-19), PAR INTERVALLE :
    entre chaque paire de `decision/rebalance` consécutifs émis à la source,
    le Δ des cumuls Mongo doit égaler la somme des événements du ledger à
    ≤ 0,01 USDT. Exige aussi chaîne de hash valide et un événement par fait.
    `consecutive_ok` (depuis la fin) pilote le tag alpha20-r0-ledger-trusted
    (règle mécanique : ≥ 3)."""
    integ = event_ledger.integrity()
    dec = event_ledger.read(kinds=["decision"])
    dec = dec[dec["ref"] == "rebalance"].reset_index(drop=True) if len(dec) else dec
    if len(dec) < 2:
        return {"status": "pending", "passed": None, "consecutive_ok": 0,
                "integrity": integ,
                "note": f"{len(dec)} rebalance(s) émis — gate évaluable à 2"}
    # seuls les événements émis À LA SOURCE comptent — les faits ré-ingérés
    # d'un audit (ref AUDIT_*) doubleraient les fenêtres qu'ils recouvrent
    fees = event_ledger.read(kinds=["fee"])
    fees = fees[~fees["ref"].astype(str).str.startswith("AUDIT_")] \
        if len(fees) else fees
    fund = event_ledger.read(kinds=["funding"])
    fund = fund[fund["ref"] == "settlement"] if len(fund) else fund
    intervals = []
    for i in range(1, len(dec)):
        a, b = dec.iloc[i - 1], dec.iloc[i]
        t0, t1 = a["ts"], b["ts"]
        d_fees = b["meta"]["mongo_fees_cum"] - a["meta"]["mongo_fees_cum"]
        d_carry = b["meta"]["mongo_carry_cum"] - a["meta"]["mongo_carry_cum"]
        lf = fees[(fees["ts"] > t0) & (fees["ts"] <= t1)] if len(fees) else fees
        lu = fund[(fund["ts"] > t0) & (fund["ts"] <= t1)] if len(fund) else fund
        gap_f = abs(float(lf["amount_usdt"].sum() if len(lf) else 0.0)
                    - float(d_fees))
        gap_c = abs(float(lu["amount_usdt"].sum() if len(lu) else 0.0)
                    - float(d_carry))
        intervals.append({"from": str(t0), "to": str(t1),
                          "gap_fees_usdt": round(gap_f, 4),
                          "gap_carry_usdt": round(gap_c, 4),
                          "passed": bool(gap_f <= GATE_USDT
                                         and gap_c <= GATE_USDT)})
    consecutive = 0
    for it in reversed(intervals):
        if not it["passed"]:
            break
        consecutive += 1
    all_ok = (all(it["passed"] for it in intervals)
              and integ["chain_ok"] and integ["one_event_per_fact"])
    return {"status": "evaluated", "gate_usdt": GATE_USDT,
            "n_rebalances": int(len(dec)), "intervals": intervals,
            "consecutive_ok": consecutive if integ["chain_ok"]
            and integ["one_event_per_fact"] else 0,
            "integrity": integ, "passed": bool(all_ok)}


def reconcile(run_fresh: bool = True) -> Dict:
    if run_fresh:
        path = run_audit()
    else:
        path = sorted((ROOT / "reports" / "paper_audit").glob("AUDIT_*.json"))[-1]
    audit = json.loads(path.read_text())
    historical = evaluate(audit)
    historical["note"] = ("trail partiellement reconstruit (limites connues "
                          "au-delà du 2026-07-18) — la vérité est le gate forward")
    n = ingest_facts(audit, audit_ref=path.name)
    fwd = forward_gate()
    result = {"historical": historical, "forward": fwd,
              "passed": fwd["passed"] if fwd["passed"] is not None
              else historical["passed"]}
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
