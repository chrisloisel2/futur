"""
src/alpha20/deployment_guard.py — garde de dérive de déploiement.

Contexte (2026-07-21) : la démotion de plusieurs runners (commitée sur le
dépôt principal) n'avait pas atteint le fichier réellement lu par le
tournoi sur la machine d'exécution pendant une durée indéterminée, sans
qu'aucun mécanisme ne le signale — le dépôt de recherche et le
comportement réel du tournoi racontaient deux histoires différentes.

Ce garde ferme cette faille au niveau de CETTE machine : il compare le
hash SHA-256 actuel de `configs/alpha20_runners.yaml` et
`configs/alpha20.yaml` contre un manifeste APPROUVÉ localement
(`configs/DEPLOYMENT_MANIFEST.json`, généré par
`scripts/generate_deployment_manifest.py`, jamais committé — c'est un état
propre à cette machine, pas une vérité partagée entre Mac et qbee : les
deux dépôts peuvent légitimement diverger dans leur contenu, ce garde ne
tranche pas cette question, il détecte seulement les changements NON
approuvés depuis la dernière génération volontaire du manifeste).

Appelé aux côtés de `src.alpha20.guard.assert_paper_only()` par chaque
entrypoint du tournoi — un module séparé plutôt qu'une extension de
`guard.py` pour ne pas toucher sa garantie déjà testée (garde structurelle
anti-trading-réel, sans rapport avec l'intégrité de déploiement).

`assert_deployment_matches_approved()` lève SystemExit(2) si le manifeste
est absent OU si un des fichiers suivis a changé depuis. Après toute
modification volontaire et relue par un humain de ces fichiers, régénérer
le manifeste avec `scripts/generate_deployment_manifest.py` -- jamais
automatiquement.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict

from src.alpha20 import ROOT

TRACKED_CONFIGS = ["configs/alpha20_runners.yaml", "configs/alpha20.yaml"]
MANIFEST_PATH = ROOT / "configs" / "DEPLOYMENT_MANIFEST.json"


class DeploymentDriftError(RuntimeError):
    pass


def _file_hash(rel_path: str) -> str:
    return hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest()


def current_hashes() -> Dict[str, str]:
    return {rel: _file_hash(rel) for rel in TRACKED_CONFIGS}


def _fail(msg: str, exit_on_fail: bool) -> None:
    if exit_on_fail:
        print(msg, file=sys.stderr)
        raise SystemExit(2)
    raise DeploymentDriftError(msg)


def assert_deployment_matches_approved(exit_on_fail: bool = True) -> None:
    if not MANIFEST_PATH.exists():
        _fail(
            f"DEPLOYMENT GUARD — aucun manifeste approuvé trouvé ({MANIFEST_PATH}). "
            "Démarrage refusé. Générer avec scripts/generate_deployment_manifest.py "
            "après revue humaine des fichiers de config suivis.",
            exit_on_fail)
        return

    manifest = json.loads(MANIFEST_PATH.read_text())
    approved = manifest.get("config_hash", {})
    live = current_hashes()
    drift = {rel: {"approved": approved.get(rel), "live": live[rel]}
            for rel in TRACKED_CONFIGS if approved.get(rel) != live[rel]}

    if drift:
        _fail(
            "DEPLOYMENT GUARD — dérive détectée entre le manifeste approuvé "
            f"({manifest.get('approved_at', '?')}, commit "
            f"{(manifest.get('git_commit') or '?')[:8]}) et les fichiers live sur "
            f"cette machine. Démarrage refusé.\n{json.dumps(drift, indent=2)}\n"
            "Si ce changement est voulu : relire le contenu, puis régénérer le "
            "manifeste avec scripts/generate_deployment_manifest.py.",
            exit_on_fail)
