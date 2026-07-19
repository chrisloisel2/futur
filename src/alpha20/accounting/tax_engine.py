"""
src/alpha20/accounting/tax_engine.py — provision fiscale par SCÉNARIO.

⚠ Scénarios de PROVISION, pas un avis fiscal. Le profil réel dépend du régime :
150 VH bis (PFU 30 % à la cession en fiat — formulaire 2086), BNC si activité
habituelle/professionnelle, régime des IFT pour certains dérivés (BOFiP
ACTU-2023-00099). Le scénario actif vient de configs/alpha20.yaml et doit être
revalidé avec un conseil avant tout passage live.

La provision est un événement du ledger (kind=tax_provision, montant négatif),
calculée sur le net POSITIF du mois — pas de crédit d'impôt provisionné sur
les mois négatifs (conservateur ; un report déficitaire éventuel se traite à
la liquidation réelle, pas dans la provision).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from src.alpha20 import load_config
from src.alpha20.contracts import LedgerEvent


def provision_for_month(net_pretax_usdt: float,
                        month: str, scenario: str = None) -> Dict:
    cfg = load_config()["tax"]
    name = scenario or cfg["active_scenario"]
    sc = cfg["scenarios"][name]
    base = max(net_pretax_usdt, 0.0)
    amount = base * float(sc["rate"])
    return {"scenario": name, "rate": float(sc["rate"]), "month": month,
            "base_usdt": round(base, 2), "provision_usdt": round(amount, 2)}


def provision_event(net_pretax_usdt: float, month: str,
                    scenario: str = None) -> LedgerEvent:
    p = provision_for_month(net_pretax_usdt, month, scenario)
    return LedgerEvent(
        ts=datetime.now(timezone.utc).isoformat(), kind="tax_provision",
        sleeve="portfolio", venue="offchain",
        amount_usdt=-p["provision_usdt"],
        ref=f"tax_{month}_{p['scenario']}", meta=p)


def required_pretax_monthly(net_target_eur: float, scenario: str = None) -> float:
    cfg = load_config()["tax"]
    name = scenario or cfg["active_scenario"]
    rate = float(cfg["scenarios"][name]["rate"])
    return net_target_eur / (1.0 - rate)
