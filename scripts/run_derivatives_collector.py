#!/usr/bin/env python3
"""
scripts/run_derivatives_collector.py
─────────────────────────────────────────────────────────────────────────────
Lance le collecteur dérivés live (Phase 1). À déployer en service systemd pour
tourner en continu — chaque jour collecté est définitivement acquis.

    python3 scripts/run_derivatives_collector.py                 # continu
    python3 scripts/run_derivatives_collector.py --duration 90   # démo 90s

⚠️ Long terme : systemctl --user (comme futur-api). Les liquidations (forceOrder)
ne sont PAS récupérables en historique → seule la collecte continue les capture.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.derivatives_collector.collector import DerivativesCollector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                   "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--rest-interval", type=int, default=300)
    ap.add_argument("--duration", type=float, default=None, help="secondes (None=continu)")
    args = ap.parse_args()

    syms = [s.strip() for s in args.symbols.split(",")]
    col = DerivativesCollector(syms, rest_interval_s=args.rest_interval)
    try:
        asyncio.run(col.run(duration_s=args.duration))
    except KeyboardInterrupt:
        pass
    print("\nHEALTH:", json.dumps(col.health, indent=2))


if __name__ == "__main__":
    main()
