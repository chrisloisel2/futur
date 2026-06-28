#!/usr/bin/env python3
"""
scripts/institutional_build_labels.py
─────────────────────────────────────────────────────────────────────────────
Construit et sauvegarde les labels institutionnels.

Doit être exécuté APRÈS institutional_build_features.py.
Les labels sont construits indépendamment des features pour éviter
toute contamination lors du split train/test.

Usage
-----
python3 scripts/institutional_build_labels.py \
    --assets BTC,ETH,SOL,BNB \
    --config configs/institutional/labels.yaml
"""
import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.loaders import load_asset_1h
from src.institutional.features.volatility import realized_vol
from src.institutional.labels.label_store import LabelStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build institutional label store")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2026-05-30")
    p.add_argument("--config", default="configs/institutional/labels.yaml")
    p.add_argument("--version", default="v1.0")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    assets = [a.strip().upper() for a in args.assets.split(",")]
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

    # Charger la config labels
    config = None
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f).get("labels", {})
        logger.info(f"Config chargée depuis {config_path}")

    logger.info(f"=== INSTITUTIONAL LABEL STORE BUILD ===")
    logger.info(f"Assets  : {assets}")
    logger.info(f"Period  : {args.start} → {args.end}")
    logger.info(f"Version : {args.version}")

    store = LabelStore(version=args.version, config=config)
    results = {}

    for asset in assets:
        if not args.force and store.exists(asset):
            logger.info(f"[{asset}] Cache hit — skip (use --force to recalculate)")
            continue

        logger.info(f"\n[{asset}] Building labels...")
        try:
            # Charger les prix
            ohlcv = load_asset_1h(asset, args.start, args.end)
            close = ohlcv["close"]

            # Calculer la vol non-annualisée (pour les barrières triple barrier)
            vol = realized_vol(close, window=24, annualize=False)

            # Charger funding si disponible
            funding = None
            try:
                from src.institutional.data.loaders import load_funding
                funding_df = load_funding(args.start, args.end)
                # As-of join pour avoir funding_rate à 1h
                from src.institutional.data.asof_join import asof_join
                joined = asof_join(ohlcv[["close"]], funding_df[["funding_rate"]])
                funding = joined.get("funding_rate")
            except Exception:
                pass

            labels = store.build(
                close=close,
                asset=asset,
                vol_series=vol,
                funding_rate=funding,
            )
            path = store.save(labels, asset)
            results[asset] = {
                "status": "OK",
                "rows": len(labels),
                "path": str(path),
            }
        except Exception as e:
            logger.error(f"[{asset}] FAILED: {e}", exc_info=True)
            results[asset] = {"status": "FAILED", "error": str(e)}

    logger.info("\n" + "=" * 60)
    n_ok = sum(1 for r in results.values() if r["status"] == "OK")
    for asset, r in results.items():
        if r["status"] == "OK":
            logger.info(f"  ✓ {asset}: {r['rows']} barres")
        else:
            logger.error(f"  ✗ {asset}: {r.get('error')}")

    logger.info(f"\n{n_ok}/{len(results)} assets OK")
    if n_ok < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
