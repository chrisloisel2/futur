"""
src/alpha20/tournament/runner_registry.py — chargeur du registre versionné.

Le fichier configs/alpha20_runners.yaml est la source déclarative ; ce module
dérive `config_hash` DE FAÇON DÉTERMINISTE depuis le bloc `config` de chaque
entrée à CHAQUE chargement — jamais une valeur stockée qui pourrait dériver
silencieusement du code réel. `git_commit`/`built_at` restent ceux stampés
par scripts/build_alpha20_registry.py (versionnage explicite, changé
uniquement sur reconstruction délibérée).

Limite les runners exécutables du tournoi à `status in (ACTIVE, OBSERVE_ONLY)`
— EXCLUDED/BLOCKED ne sont JAMAIS instanciés, même par erreur.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

from src.alpha20 import ROOT

REGISTRY_PATH = ROOT / "configs" / "alpha20_runners.yaml"
RUNNABLE_STATUSES = ("ACTIVE", "OBSERVE_ONLY")
VALID_STATUSES = ("ACTIVE", "OBSERVE_ONLY", "BLOCKED", "EXCLUDED")


@dataclass
class RunnerSpec:
    runner_id: str
    family: str
    status: str
    git_commit: str
    config_hash: Optional[str]
    assets: object = None
    venue: Optional[str] = None
    frequency: Optional[str] = None
    features_model: Optional[str] = None
    sizing: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    justification: Optional[str] = None
    source: Optional[str] = None

    @property
    def runnable(self) -> bool:
        return self.status in RUNNABLE_STATUSES

    @property
    def capital_standalone_eur(self) -> float:
        return float(self.sizing.get("capital_standalone_eur", 200000.0))


def _config_hash(cfg: dict) -> Optional[str]:
    if not cfg:
        return None
    return hashlib.sha256(
        json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:24]


def load_registry() -> Dict[str, RunnerSpec]:
    raw = yaml.safe_load(REGISTRY_PATH.read_text())
    git_commit = raw.get("git_commit", "unknown")
    specs = {}
    ids_seen = set()
    for r in raw.get("runners", []):
        rid = r["runner_id"]
        if rid in ids_seen:
            raise ValueError(f"runner_id dupliqué dans le registre: {rid}")
        ids_seen.add(rid)
        status = r.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"{rid}: status invalide {status!r}")
        if status in RUNNABLE_STATUSES and not r.get("config"):
            raise ValueError(f"{rid}: status {status} exige un bloc `config`")
        if status in ("EXCLUDED", "BLOCKED") and not r.get("justification"):
            raise ValueError(f"{rid}: status {status} exige une `justification`")
        specs[rid] = RunnerSpec(
            runner_id=rid, family=r.get("family", "unknown"), status=status,
            git_commit=git_commit, config_hash=_config_hash(r.get("config", {})),
            assets=r.get("assets"), venue=r.get("venue"),
            frequency=r.get("frequency"), features_model=r.get("features_model"),
            sizing=r.get("sizing", {}), dependencies=r.get("dependencies", []),
            config=r.get("config", {}), justification=r.get("justification"),
            source=r.get("source"))
    return specs


def runnable_specs() -> List[RunnerSpec]:
    return [s for s in load_registry().values() if s.runnable]


def get_spec(runner_id: str) -> RunnerSpec:
    reg = load_registry()
    if runner_id not in reg:
        raise KeyError(f"runner inconnu: {runner_id}")
    return reg[runner_id]
