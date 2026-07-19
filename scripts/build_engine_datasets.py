#!/usr/bin/env python3
"""
scripts/build_engine_datasets.py
─────────────────────────────────────────────────────────────────────────────
Construit les datasets séparés par moteur.

Un moteur = un dataset dédié.
Ne jamais mixer BTC trend avec SOL event dans le même dataset.

COMPORTEMENT EN CAS D'ERREUR :
    - Asset explicitement demandé qui échoue → exit code 1 + PARTIAL_FAILED
    - Rapport écrit dans artifacts/institutional/datasets/{engine}/build_report.json
    - failed_assets.json écrit si des assets ont échoué

Usage :
    python3 scripts/build_engine_datasets.py --engines btc_eth_trend,trm_event \\
        --start 2021-01-01 --end 2025-12-31 --check-labels

Sorties :
    artifacts/institutional/datasets/btc_eth_trend/{ASSET}_2021_2025.parquet
    artifacts/institutional/datasets/btc_eth_trend/build_report.json
    artifacts/institutional/datasets/btc_eth_trend/failed_assets.json  (si échec)
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.dataset_builder import (
    EngineDatasetBuilder,
    btc_eth_trend_config,
    trm_event_config,
    carry_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


AVAILABLE_ENGINES = {
    "btc_eth_trend": btc_eth_trend_config,
    "trm_event":     trm_event_config,
    "carry":         carry_config,
}

ARTIFACTS_ROOT = Path("artifacts/institutional/datasets")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build per-engine datasets")
    p.add_argument("--engines", default="btc_eth_trend",
                   help=f"Comma-separated or 'all'. Available: {list(AVAILABLE_ENGINES)}")
    p.add_argument("--start",        default="2021-01-01")
    p.add_argument("--end",          default="2025-12-31")
    p.add_argument("--check-labels", action="store_true")
    p.add_argument("--force",        action="store_true")
    p.add_argument("--no-validate",  action="store_true")
    return p.parse_args()


def check_label_distribution(datasets: dict, engine_name: str) -> None:
    print(f"\n{'─'*65}")
    print(f"DISTRIBUTIONS DE LABELS — {engine_name}")
    print(f"{'─'*65}")
    for asset, df in datasets.items():
        label_cols = [c for c in df.columns
                      if any(c.startswith(p) for p in ["trend_cont_", "event_cont_", "carry_net_"])]
        for col in label_cols:
            valid = df[col].dropna()
            if len(valid) == 0:
                continue
            up   = float((valid == 1).mean())
            flat = float((valid == 0).mean())
            down = float((valid == -1).mean())

            if flat < 0.30:
                status = "⚠ FLAT<30% — k trop petit, modèle prédit du bruit"
            elif flat > 0.85:
                status = "⚠ FLAT>85% — k trop grand, signal rare"
            elif max(up, down) > flat:
                status = "⚠ UP|DOWN > FLAT — vérifier le threshold"
            else:
                status = "OK"

            print(f"  {asset:12s} {col:25s}: "
                  f"UP={up:.1%} FLAT={flat:.1%} DOWN={down:.1%} "
                  f"n={len(valid):>6,}  [{status}]")


def write_build_report(
    engine_name: str,
    config_assets: list,
    built_assets: list,
    failed_assets: list,
    durations: dict,
    args: argparse.Namespace,
) -> None:
    """Écrit build_report.json et failed_assets.json dans le dossier du moteur."""
    out_dir = ARTIFACTS_ROOT / engine_name.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "engine":           engine_name,
        "start":            args.start,
        "end":              args.end,
        "requested_assets": config_assets,
        "built_assets":     built_assets,
        "failed_assets":    failed_assets,
        "n_requested":      len(config_assets),
        "n_built":          len(built_assets),
        "n_failed":         len(failed_assets),
        "status":           "OK" if not failed_assets else "PARTIAL_FAILED",
        "durations_s":      durations,
    }

    report_path = out_dir / "build_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info(f"  Build report: {report_path}")

    if failed_assets:
        failed_path = out_dir / "failed_assets.json"
        failed_path.write_text(json.dumps({"failed": failed_assets}, indent=2))
        logger.error(f"  Failed assets: {failed_path}")


def main() -> None:
    args = parse_args()

    if args.engines == "all":
        engines_to_build = list(AVAILABLE_ENGINES.keys())
    else:
        engines_to_build = [e.strip() for e in args.engines.split(",")]

    unknown = [e for e in engines_to_build if e not in AVAILABLE_ENGINES]
    if unknown:
        logger.error(f"Moteurs inconnus: {unknown}. Disponibles: {list(AVAILABLE_ENGINES)}")
        sys.exit(1)

    logger.info(f"Moteurs : {engines_to_build}")
    logger.info(f"Période : {args.start} → {args.end}")

    builder      = EngineDatasetBuilder()
    global_ok    = True
    global_fails: dict[str, list] = {}   # engine → failed assets

    for engine_name in engines_to_build:
        logger.info(f"\n{'='*55}")
        logger.info(f"Moteur : {engine_name.upper()}")

        config = AVAILABLE_ENGINES[engine_name](start=args.start, end=args.end)
        config_assets = list(config.assets)

        built_assets  = []
        failed_assets = []
        durations     = {}

        # ── Construire asset par asset pour tracer les échecs ──────────────────
        for asset in config_assets:
            t0 = time.time()
            try:
                from src.institutional.data.dataset_builder import EngineDatasetConfig
                single_config = EngineDatasetConfig(
                    engine_name=config.engine_name,
                    assets=[asset],
                    start=config.start,
                    end=config.end,
                    feature_families=config.feature_families,
                    label_family=config.label_family,
                    label_horizons_h=config.label_horizons_h,
                    label_k=config.label_k,
                    label_cost_bps=config.label_cost_bps,
                    include_funding=config.include_funding,
                    include_oi=config.include_oi,
                )
                asset_datasets = builder.build(
                    single_config,
                    validate_quality=not args.no_validate,
                )
                if asset in asset_datasets and len(asset_datasets[asset]) > 0:
                    # Sauvegarder immédiatement
                    builder.save({asset: asset_datasets[asset]}, config)
                    built_assets.append(asset)
                    logger.info(f"  ✓ {asset}: {len(asset_datasets[asset]):,} barres "
                                f"× {len(asset_datasets[asset].columns)} cols")
                    if args.check_labels:
                        check_label_distribution({asset: asset_datasets[asset]}, engine_name)
                else:
                    failed_assets.append(asset)
                    logger.error(f"  ✗ {asset}: dataset vide après construction")

            except Exception as e:
                failed_assets.append(asset)
                logger.error(f"  ✗ {asset}: {e}")

            durations[asset] = round(time.time() - t0, 1)

        # ── Évaluation et rapport ──────────────────────────────────────────────
        write_build_report(
            engine_name, config_assets, built_assets, failed_assets, durations, args
        )

        # ── Verdict ───────────────────────────────────────────────────────────
        if failed_assets:
            global_ok = False
            global_fails[engine_name] = failed_assets
            logger.error(
                f"\n  PARTIAL_FAILED — {len(failed_assets)}/{len(config_assets)} assets échoués : "
                f"{failed_assets}"
            )
        else:
            logger.info(
                f"\n  ✓ {len(built_assets)}/{len(config_assets)} assets construits avec succès"
            )

    # ── Bilan global ──────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    if global_ok:
        print("TOUS LES DATASETS CONSTRUITS ✓")
    else:
        print("PARTIAL_FAILED — assets échoués par moteur :")
        for eng, fails in global_fails.items():
            print(f"  {eng}: {fails}")
        print()
        print("Actions correctives :")
        print("  1. Vérifier la disponibilité des sources :")
        print("     python3 scripts/debug_asset_schema.py --assets <ASSET>")
        print("  2. Placer les fichiers manquants dans data/enriched/")
        print("  3. Relancer avec --force")
        sys.exit(1)


if __name__ == "__main__":
    main()
