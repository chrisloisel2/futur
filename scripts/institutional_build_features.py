#!/usr/bin/env python3
"""
scripts/institutional_build_features.py
─────────────────────────────────────────────────────────────────────────────
Construit et sauvegarde les features institutionnelles.

Usage
-----
python3 scripts/institutional_build_features.py \
    --assets BTC,ETH,SOL,BNB \
    --start 2021-01-01 \
    --end 2026-05-30 \
    --version v1.0

Sortie : artifacts/institutional/features/v1.0/{ASSET}_features.parquet
"""
import argparse
import logging
import sys
from pathlib import Path

# Rendre le package src importable
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.features.feature_store import FeatureStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build institutional feature store")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT",
                   help="Comma-separated assets (e.g. BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT)")
    p.add_argument("--start", default="2021-01-01", help="Start date YYYY-MM-DD")
    p.add_argument("--end", default="2026-05-30", help="End date YYYY-MM-DD")
    p.add_argument("--version", default="v1.0", help="Feature store version")
    p.add_argument("--no-funding", action="store_true", help="Skip funding features")
    p.add_argument("--no-metrics", action="store_true", help="Skip OI/LSR features")
    p.add_argument("--no-validate", action="store_true", help="Skip data quality validation")
    p.add_argument("--force", action="store_true", help="Recalculate even if cache exists")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    assets = [a.strip().upper() for a in args.assets.split(",")]
    # Normaliser les noms d'actifs (ajouter USDT si manquant)
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

    logger.info(f"=== INSTITUTIONAL FEATURE STORE BUILD ===")
    logger.info(f"Assets  : {assets}")
    logger.info(f"Period  : {args.start} → {args.end}")
    logger.info(f"Version : {args.version}")

    store = FeatureStore(version=args.version)
    results = {}

    for asset in assets:
        if not args.force and store.exists(asset):
            logger.info(f"[{asset}] Cache hit — skip (use --force to recalculate)")
            continue

        logger.info(f"\n[{asset}] Building features...")
        try:
            features = store.build(
                asset=asset,
                start=args.start,
                end=args.end,
                include_funding=not args.no_funding,
                include_metrics=not args.no_metrics,
                validate_quality=not args.no_validate,
            )
            path = store.save(features, asset)
            results[asset] = {
                "status": "OK",
                "rows": len(features),
                "cols": len(features.columns),
                "path": str(path),
            }
        except Exception as e:
            logger.error(f"[{asset}] FAILED: {e}")
            results[asset] = {"status": "FAILED", "error": str(e)}

    # Rapport final
    logger.info("\n" + "=" * 60)
    logger.info("RÉSULTATS:")
    n_ok = 0
    for asset, r in results.items():
        status = r["status"]
        if status == "OK":
            n_ok += 1
            logger.info(f"  ✓ {asset}: {r['rows']} barres × {r['cols']} features")
        else:
            logger.error(f"  ✗ {asset}: {r.get('error', 'unknown error')}")

    logger.info(f"\n{n_ok}/{len(results)} assets OK")

    if n_ok < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
