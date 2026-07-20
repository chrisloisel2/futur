#!/usr/bin/env python3
"""scripts/run_alpha20_tournament.py — un cycle du tournoi ALPHA_20 (bus,
runners isolés, broker paper, ledgers). Voir src.alpha20.tournament.orchestrator."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.alpha20.tournament.orchestrator import run_cycle

if __name__ == "__main__":
    run_cycle()
