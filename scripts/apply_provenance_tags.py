#!/usr/bin/env python3
"""
scripts/apply_provenance_tags.py
─────────────────────────────────────────────────────────────────────────────
Applique (ou recalcule idempotemment) la colonne `provenance`
(REPLAY / FORWARD_LIVE) sur tous les ledgers reports/live_alpha_lab/*/decisions.parquet,
en comparant la colonne temps de l'ÉVÉNEMENT (pas `decided_at`) au
freeze_timestamp COURANT de l'alpha dans configs/live_alpha_registry.yaml.

Idempotent et sûr à relancer après chaque run des scripts run_*_shadow.py, ou
après toute réinitialisation de freeze_timestamp (auquel cas tout le volume
déjà écrit redevient REPLAY par construction — c'est le comportement voulu,
voir la correction de discipline du 2026-08-31 dans live_alpha_registry.yaml).
Ne modifie jamais que la colonne `provenance` — aucune autre valeur touchée.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import (
    PRE_COMMIT_DISCIPLINE, provenance_counts, tag_provenance)

REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
LAB_DIR = ROOT / "reports" / "live_alpha_lab"

# Colonne temps de l'ÉVÉNEMENT par alpha_id (PAS decided_at). Explicite, pas
# devinée : chaque alpha a un schéma différent selon sa famille.
TIME_COL_BY_ALPHA = {
    "LIQ_CASCADE_REPEAT_V1": "event_time",
    "LIQ_CASCADE_FAR_FROM_LOW_V1": "event_time",
    "SHORT_COVERING_CONTINUATION_V1": "timestamp",
    "WHALE_LSR_SCREEN_V1": "timestamp",
    "FUNDING_BASIS_DISAGREEMENT_V1": "date",
    "FUNDING_BASIS_DISAGREEMENT_V2": "date",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": "event_time",
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": "event_time",
    "VOL_FORECAST_LAYER_V1": "event_time",
    "AMIHUD_ILLIQUIDITY_PREMIUM_V1": "event_time",
}


def main() -> int:
    reg = yaml.safe_load(REGISTRY.read_text())
    by_id = {a["alpha_id"]: a for a in reg["alphas"]}

    for alpha_dir in sorted(LAB_DIR.iterdir()):
        if not alpha_dir.is_dir():
            continue
        alpha_id = alpha_dir.name
        ledger = alpha_dir / "decisions.parquet"
        if not ledger.exists():
            continue
        entry = by_id.get(alpha_id)
        if entry is None or not entry.get("freeze_timestamp"):
            print(f"[{alpha_id}] pas d'entrée figée dans le registre (freeze_timestamp manquant) — skip")
            continue
        time_col = TIME_COL_BY_ALPHA.get(alpha_id)
        if time_col is None:
            print(f"[{alpha_id}] TIME_COL_BY_ALPHA non renseigné — skip (ajouter explicitement, jamais deviner)")
            continue

        df = pd.read_parquet(ledger)
        if time_col not in df.columns:
            print(f"[{alpha_id}] colonne {time_col!r} absente du ledger — skip")
            continue
        tagged = tag_provenance(df, time_col, entry["freeze_timestamp"])
        if "code_commit_sha" not in tagged.columns:
            tagged["code_commit_sha"] = PRE_COMMIT_DISCIPLINE
        else:
            tagged["code_commit_sha"] = tagged["code_commit_sha"].fillna(PRE_COMMIT_DISCIPLINE)
        tagged.to_parquet(ledger, index=False)
        counts = provenance_counts(tagged)
        print(f"[{alpha_id}] freeze={entry['freeze_timestamp']} -> "
              f"replay={counts['replay_decisions']} forward={counts['forward_decisions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
