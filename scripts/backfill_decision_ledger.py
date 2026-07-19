#!/usr/bin/env python3
"""
scripts/backfill_decision_ledger.py
─────────────────────────────────────────────────────────────────────────────
Rejoue l'historique : fait tourner les moteurs sur une fenêtre, écrit une ligne
de décision par heure × asset × moteur (trades ET non-trades) dans le
DecisionLedger, puis réconcilie les forward returns depuis les prix enrichis.

Transforme le silence passé du modèle en dataset d'apprentissage.

Usage :
    python3 scripts/backfill_decision_ledger.py \
        --engines TRM_TREND_LONG,TRM_TREND_INST --start 2024-01-01 --end 2026-06-20
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.registry import build_engine
from src.institutional.engines.legacy_bridge import load_enriched
from src.institutional.monitoring.decision_ledger import DecisionLedger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("backfill")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="TRM_TREND_LONG,TRM_TREND_INST")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-06-20")
    ap.add_argument("--ledger", default=None, help="chemin parquet (défaut artifacts)")
    args = ap.parse_args()

    ledger = DecisionLedger(Path(args.ledger) if args.ledger else None)
    engine_ids = [e.strip() for e in args.engines.split(",") if e.strip()]

    assets_seen = set()
    total = 0
    for eid in engine_ids:
        try:
            eng = build_engine(eid)
        except Exception as e:
            logger.warning("moteur %s indisponible: %s", eid, e)
            continue
        logger.info("── %s (status=%s) ──", eid, eng.status)
        for asset in eng.assets:
            opps = eng.generate(asset, args.start, args.end)
            tau_a, tau_b = eng.thresholds_for(asset)
            for opp in opps:
                ledger.record(opp, tau_a=tau_a, tau_b=tau_b)
            assets_seen.add(asset)
            total += len(opps)
            logger.info("   %-10s %s: %d décisions", eid, asset, len(opps))

    n = ledger.flush()
    logger.info("Flush : %d nouvelles lignes (total buffer)", n)

    # réconciliation forward returns depuis prix enrichis
    prices = {}
    for asset in sorted(assets_seen):
        df = load_enriched(asset, required_cols=["close"], start=args.start, end=args.end)
        if df is not None and not df.empty:
            prices[asset] = df.set_index("datetime")["close"]
    if prices:
        nrec = ledger.reconcile_forward_returns(prices)
        logger.info("Reconcile : %d lignes avec forward returns", nrec)

    import json
    print("\n=== LEDGER SUMMARY ===")
    print(json.dumps(ledger.summary(), indent=2))


if __name__ == "__main__":
    main()
