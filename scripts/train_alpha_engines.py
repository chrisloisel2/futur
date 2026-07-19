#!/usr/bin/env python3
"""
scripts/train_alpha_engines.py
─────────────────────────────────────────────────────────────────────────────
Entraîne les moteurs ML (walk-forward par fold) : Pullback, Liquidation, Carry.
Cross-sectional est heuristique (pas d'entraînement). TRM_* sont des wrappers.

Usage :
    python3 scripts/train_alpha_engines.py --engines PULLBACK_LONG,LIQUIDATION_REBOUND,CARRY_BASIS
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.engines.registry import build_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("train_alpha")

TRAINABLE = {"PULLBACK_LONG", "LIQUIDATION_REBOUND", "CARRY_BASIS"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="PULLBACK_LONG,LIQUIDATION_REBOUND,CARRY_BASIS")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--n-estimators", type=int, default=300)
    args = ap.parse_args()

    reports = {}
    for eid in [e.strip() for e in args.engines.split(",") if e.strip()]:
        if eid not in TRAINABLE:
            logger.info("%s : pas d'entraînement requis (wrapper/heuristique), skip", eid)
            continue
        eng = build_engine(eid)
        logger.info("════ entraînement %s ════", eid)
        rep = eng.train(start=args.start, end=args.end, n_estimators=args.n_estimators)
        reports[eid] = rep

    out = Path("artifacts/institutional/engines/training_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, indent=2, default=str))
    print(f"\n→ rapport entraînement: {out}")
    for eid, rep in reports.items():
        for asset, folds in rep.get("assets", {}).items():
            aucs = [f["auc"] for f in folds]
            med = sorted(aucs)[len(aucs)//2] if aucs else 0
            print(f"  {eid:<22} {asset:<9} folds={len(folds)} AUC_med={med:.3f}")


if __name__ == "__main__":
    main()
