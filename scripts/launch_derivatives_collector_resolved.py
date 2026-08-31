#!/usr/bin/env python3
"""
scripts/launch_derivatives_collector_resolved.py
─────────────────────────────────────────────────────────────────────────────
Résout l'univers figé (configs/portfolio_v1_1_parallel_50.yaml) contre les
métadonnées exchange LIVE avant de lancer le collecteur dérivés — corrige le
bug du 2026-08-31 où run_derivatives_collector.py recevait la liste canonique
brute (MKRUSDT/PEPEUSDT/RNDRUSDT échouaient silencieusement, aucune trace).

Écrit un manifeste explicite AVANT chaque lancement
(data/derivatives_raw/_symbol_resolution.json) puis exec le collecteur avec
UNIQUEMENT les exchange_symbol éligibles. Un instrument non éligible n'est
JAMAIS juste absent : il apparaît dans le manifeste avec son
instrument_status réel et sa raison.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.data.derivatives_collector.symbol_resolver import resolve_universe

UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
MANIFEST = ROOT / "data" / "derivatives_raw" / "_symbol_resolution.json"


def main() -> None:
    universe = sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])
    resolved = resolve_universe(universe)

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "universe_config": str(UNIVERSE_CONFIG.relative_to(ROOT)),
        "n_canonical": len(resolved),
        "n_eligible": sum(r.eligible for r in resolved),
        "symbols": [
            {
                "canonical_asset": r.canonical_asset,
                "exchange_symbol": r.exchange_symbol,
                "instrument_status": r.instrument_status,
                "eligibility_reason": r.eligibility_reason,
                "eligible": r.eligible,
            }
            for r in resolved
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))

    ineligible = [r for r in resolved if not r.eligible]
    for r in ineligible:
        print(f"[symbol_resolver] INELIGIBLE {r.canonical_asset}: "
              f"{r.instrument_status} -- {r.eligibility_reason}", flush=True)
    renamed = [r for r in resolved if r.instrument_status == "RENAMED"]
    for r in renamed:
        print(f"[symbol_resolver] RENAMED {r.canonical_asset} -> {r.exchange_symbol}", flush=True)

    eligible_symbols = [r.exchange_symbol for r in resolved if r.eligible]
    print(f"[symbol_resolver] {len(eligible_symbols)}/{len(resolved)} éligibles -> "
          f"lancement du collecteur", flush=True)

    os.execv(sys.executable, [
        sys.executable, str(ROOT / "scripts" / "run_derivatives_collector.py"),
        "--symbols", ",".join(eligible_symbols),
        "--rest-interval", "300",
    ])


if __name__ == "__main__":
    main()
