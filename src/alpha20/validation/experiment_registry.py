"""
src/alpha20/validation/experiment_registry.py — mémoire des verdicts (étape 1/10).

Garde-fou contre le recyclage : toute hypothèse déjà classée NO_EDGE/REJECTED
(configs/alpha20.yaml → experiment_registry) est REFUSÉE sauf thèse nouvelle
pré-enregistrée explicitement. funding_xvenue_v0 ne doit JAMAIS être recyclé
sous un autre nom (ordre du 2026-07-19).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.alpha20 import load_config


class RecycledExperimentError(RuntimeError):
    pass


def _registry() -> Dict:
    return load_config()["experiment_registry"]


def closed_names() -> List[str]:
    reg = _registry()
    return [e["name"] for e in
            reg.get("closed_no_edge", []) + reg.get("not_testable", [])]


def lookup(name: str) -> Optional[Dict]:
    for section in ("closed_no_edge", "not_testable", "validated_foundations"):
        for e in _registry().get(section, []):
            if e["name"] == name:
                return dict(e, section=section)
    return None


def guard_new_experiment(name: str, new_thesis: str = "") -> Dict:
    """À appeler AVANT tout nouveau protocole. Lève si l'idée est déjà classée
    et qu'aucune thèse nouvelle n'est fournie ; funding_xvenue est verrouillé
    définitivement quelle que soit la thèse."""
    if "xvenue" in name.lower():
        raise RecycledExperimentError(
            f"{name} : funding_xvenue est classé NO_EDGE définitif (1717fd8) — "
            "ne jamais recycler, même sous un autre nom")
    hit = lookup(name)
    if hit is None:
        return {"name": name, "status": "new"}
    if hit["section"] == "validated_foundations":
        return dict(hit, status="foundation")
    if not new_thesis.strip():
        raise RecycledExperimentError(
            f"{name} : déjà classé {hit.get('verdict')} ({hit.get('ref')}) — "
            "re-test interdit sans thèse nouvelle pré-enregistrée")
    return dict(hit, status="reopened_with_thesis", new_thesis=new_thesis)
