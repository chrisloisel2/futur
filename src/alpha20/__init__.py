"""
ALPHA_20 — couche indépendante au-dessus de src/institutional (2026-07-19).

Transforme plusieurs sleeves décorrélés en rendement réellement NET (frais,
slippage, impact, borrow, gas, infra, provision fiscale déduits). Consomme les
signaux existants par adaptateurs ; ne modifie ni V1.1, ni le shadow, ni leurs
ledgers. Vérité économique : configs/alpha20.yaml. Journal : event ledger
append-only (accounting/event_ledger.py) — jamais d'agrégats Mongo reconstruits.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "alpha20.yaml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())
