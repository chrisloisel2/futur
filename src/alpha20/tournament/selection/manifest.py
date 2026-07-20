"""
src/alpha20/tournament/selection/manifest.py — chargeur du protocole FIGÉ.

`protocol_hash` doit être IDENTIQUE entre le début de la phase B d'un runner
et sa phase D — toute modification du protocole après coup invalide la
promotion en cours (même logique que config_hash pour les runners).
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional

import yaml

from src.alpha20 import ROOT

PROTOCOL_PATH = ROOT / "configs" / "alpha20_selection_protocol.yaml"


def load_protocol() -> Dict:
    return yaml.safe_load(PROTOCOL_PATH.read_text())


def protocol_hash() -> str:
    p = dict(load_protocol())
    p.pop("frozen_at", None)          # le stamp temporel n'est pas un contenu
    return hashlib.sha256(
        json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()[:24]


def min_decisions_for(runner_id: str) -> int:
    proto = load_protocol()
    override = proto.get("runner_overrides", {}).get(runner_id, {})
    return int(override.get("min_decisions",
                            proto["phase_b_observation"]["min_decisions"]))
