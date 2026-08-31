#!/usr/bin/env python3
"""
scripts/refresh_deribit_options_live.py
─────────────────────────────────────────────────────────────────────────────
Rafraîchit data/options_backfill/deribit/ (trades BTC + DVOL BTC/ETH +
features quotidiennes BTC) — reprise d'une collecte déjà approuvée (Live
Alpha Lab, configs/live_alpha_registry.yaml, familles OPTIONS_*_V1), PAS une
nouvelle découverte de donnée. Léger (~100-200MB/mois) : pas de risque disque
comparable au collecteur L2 market_physics_v3 (volontairement PAS relancé
ici, voir project_live_alpha_lab.md pour la raison).

Chaque étape est déjà incrémentale par construction (backfill_deribit_option_trades.py
skip les mois déjà complets sauf --force ; dvol/features réécrivent l'historique
complet mais restent petits : DVOL ~2 séries quotidiennes, features ~1 fichier/jour).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python3"


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, timeout=1800)
    if r.returncode != 0:
        print(f"  -> exit {r.returncode} (non bloquant, étape suivante)", flush=True)


def main() -> int:
    run([str(PY), "scripts/backfill_deribit_option_trades.py", "--currency", "BTC"])
    run([str(PY), "scripts/backfill_deribit_dvol.py"])
    run([str(PY), "scripts/build_deribit_positioning_features.py", "--currency", "BTC"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
