#!/usr/bin/env python3
"""
scripts/institutional_paper_trade.py
─────────────────────────────────────────────────────────────────────────────
Lance le paper trading en mode live shadow (ne touche pas au capital réel).

Le script tourne en boucle, polling les données en temps réel.
Chaque signal est logué avec son contexte complet.

Gates de promotion vers live (non négociables) :
  - 90 jours | 100 trades | PF > 1.15 | DD < 3%

Usage
-----
python3 scripts/institutional_paper_trade.py \
    --portfolio meta_v1 \
    --assets BTCUSDT,ETHUSDT \
    --version v1.0 \
    --mode shadow   # shadow = observe sans ordres | active = simule fills
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.monitoring.paper_trading import PaperTradingLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--portfolio", default="meta_v1")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT")
    p.add_argument("--version", default="v1.0")
    p.add_argument("--mode", default="shadow", choices=["shadow", "active"])
    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--interval-seconds", type=int, default=3600,
                   help="Intervalle entre les runs (secondes). 3600 = 1h")
    p.add_argument("--once", action="store_true", help="Exécuter une seule fois")
    return p.parse_args()


def run_once(args, paper_log: PaperTradingLog) -> None:
    """Exécute un cycle de paper trading."""
    import pandas as pd

    try:
        from src.institutional.data.loaders import load_asset_1h
        from src.institutional.features.feature_store import FeatureStore

        assets = [a.strip().upper() for a in args.assets.split(",")]
        assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

        now = pd.Timestamp.utcnow()
        start = (now - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")

        fs = FeatureStore(version=args.version)

        for asset in assets:
            try:
                # Reconstruire les features avec les données les plus récentes
                features = fs.build(
                    asset=asset,
                    start=start,
                    end=end,
                    validate_quality=True,
                )
                if features.empty:
                    continue

                # Charger le modèle
                model_path = (
                    Path("artifacts/institutional/models") / "trend_following" / asset
                    / f"{args.version}_lgbm.pkl"
                )
                if not model_path.exists():
                    logger.debug(f"  {asset}: modèle non trouvé — skip")
                    continue

                from src.institutional.models.tree.lightgbm_model import LightGBMClassifier
                model = LightGBMClassifier.load(model_path)

                # Dernière barre de features
                meta_cols = ["asset", "feature_version", "label_version", "config_hash"]
                X = features.drop(columns=meta_cols, errors="ignore").iloc[[-1]]

                proba = model.predict_proba(X)

                # Logs ici — dans un système live, on enverrait l'ordre
                logger.info(
                    f"  [{asset}] proba={proba[0].round(3)} "
                    f"dir={model._label_encoder.inverse_transform([proba[0].argmax()])[0]}"
                )

            except Exception as e:
                logger.warning(f"  [{asset}] {e}")

    except Exception as e:
        logger.error(f"run_once failed: {e}", exc_info=True)

    # Afficher l'état paper trading
    summary = paper_log.get_summary()
    gates = summary["gates"]
    logger.info(f"\n=== PAPER TRADING STATUS ===")
    logger.info(f"  Run ID      : {summary['run_id']}")
    logger.info(f"  Days running: {summary['days_running']}")
    logger.info(f"  N trades    : {summary['n_trades']}")
    logger.info(f"  PF          : {summary['pf']}")
    logger.info(f"  Drawdown    : {summary['drawdown']:.2%}")
    logger.info(f"  Gate status : {gates}")
    logger.info(f"  LIVE READY  : {gates['ready_for_live']}")


def main() -> None:
    args = parse_args()

    paper_log = PaperTradingLog(
        run_id=args.portfolio,
        initial_equity=args.initial_equity,
    )

    logger.info(f"=== INSTITUTIONAL PAPER TRADING ===")
    logger.info(f"Portfolio : {args.portfolio}")
    logger.info(f"Mode      : {args.mode}")
    logger.info(f"Interval  : {args.interval_seconds}s")

    if args.once:
        run_once(args, paper_log)
        return

    while True:
        run_once(args, paper_log)
        logger.info(f"Prochain run dans {args.interval_seconds}s...")
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
