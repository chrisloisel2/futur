#!/usr/bin/env python3
"""
scripts/institutional_run_backtest.py
─────────────────────────────────────────────────────────────────────────────
Backtest événementiel complet avec exécution simulée.

Produit :
  artifacts/institutional/backtests/{portfolio}/{version}/
    ├── trades.parquet
    ├── equity_curve.parquet
    ├── metrics.json
    └── report.md

Usage
-----
python3 scripts/institutional_run_backtest.py \
    --portfolio institutional_v1 \
    --assets BTCUSDT,ETHUSDT \
    --start 2022-01-01 \
    --end 2025-12-31 \
    --version v1.0
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pandas as pd
import numpy as np

from src.institutional.features.feature_store import FeatureStore
from src.institutional.labels.label_store import LabelStore
from src.institutional.models.tree.lightgbm_model import LightGBMClassifier
from src.institutional.execution.execution_simulator import ExecutionSimulator, ExecutionConfig
from src.institutional.backtest.metrics import compute_equity_metrics, stress_test_costs
from src.institutional.contracts import RiskState, PortfolioState, Position, SignalFrame
from src.institutional.risk.risk_engine import RiskEngine, RiskConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--portfolio", default="institutional_v1")
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--version", default="v1.0")
    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--cost-bps", type=float, default=10.0,
                   help="Coût aller-retour en bps")
    return p.parse_args()


def simple_signal_from_proba(
    proba: np.ndarray,
    timestamp: pd.Timestamp,
    asset: str,
    label_encoder,
    min_confidence: float = 0.60,
    model_version: str = "v1.0",
) -> SignalFrame:
    """Convertit des probabilités LightGBM en SignalFrame."""
    if proba.ndim == 2:
        class_idx = proba.argmax()
        prob_class = proba[class_idx]
        label = label_encoder.inverse_transform([class_idx])[0]
    else:
        class_idx = int(proba > 0.5)
        prob_class = proba if class_idx == 1 else 1 - proba
        label = class_idx

    confidence = float(prob_class)

    if confidence < min_confidence or label == 0:
        direction = "flat"
    elif label == 1:
        direction = "long"
    else:
        direction = "short"

    return SignalFrame(
        timestamp=timestamp,
        asset=asset,
        engine_name="INSTITUTIONAL_ENGINE",
        signal_name="trend_following_lgbm",
        direction=direction,
        raw_score=float(proba.max() if proba.ndim == 2 else proba),
        calibrated_score=confidence,
        confidence=confidence,
        expected_return=float(confidence * 0.02),
        expected_vol=0.20,
        horizon_minutes=24 * 60,
        max_holding_minutes=72 * 60,
        stop_distance=0.02,
        take_profit_distance=0.04,
        model_version=model_version,
        feature_version=model_version,
        label_version=model_version,
        run_id="backtest",
    )


def main() -> None:
    args = parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",")]
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

    out_dir = Path("artifacts/institutional/backtests") / args.portfolio / args.version
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"=== INSTITUTIONAL BACKTEST ===")
    logger.info(f"Portfolio : {args.portfolio}")
    logger.info(f"Assets    : {assets}")
    logger.info(f"Period    : {args.start} → {args.end}")
    logger.info(f"Cost      : {args.cost_bps} bps AR")

    fs = FeatureStore(version=args.version)
    ls = LabelStore(version=args.version)

    exec_config = ExecutionConfig(
        taker_fee_bps=args.cost_bps / 2,
        fixed_slippage_bps=args.cost_bps / 2,
    )
    sim = ExecutionSimulator(exec_config)

    equity = args.initial_equity
    equity_curve = []
    all_trades = []

    for asset in assets:
        logger.info(f"\n[{asset}] Backtesting...")

        try:
            features = fs.load(asset)
            labels = ls.load(asset)
        except FileNotFoundError:
            logger.warning(f"  Features/labels non trouvés pour {asset} — skip")
            continue

        # Utiliser uniquement la période de test
        features = features.loc[args.start:args.end]
        labels = labels.loc[args.start:args.end]

        # Charger le modèle entraîné (si disponible)
        model_path = (
            Path("artifacts/institutional/models") / "trend_following" / asset
            / f"{args.version}_lgbm.pkl"
        )

        if not model_path.exists():
            logger.warning(f"  Modèle non trouvé : {model_path} — utiliser institutional_train_models.py d'abord")
            continue

        model = LightGBMClassifier.load(model_path)

        # Préparer les features de test
        meta_cols = ["asset", "feature_version", "label_version", "config_hash"]
        X_test = features.drop(columns=meta_cols, errors="ignore")

        # Prédictions
        proba = model.predict_proba(X_test)

        # Simulation simple barre par barre
        position = None
        entry_price = None
        entry_ts = None

        for i, (ts, row) in enumerate(X_test.iterrows()):
            p = proba[i]
            signal = simple_signal_from_proba(
                p, ts, asset, model._label_encoder, model_version=args.version
            )

            # Fermer position si signal plat ou inversé
            if position is not None:
                if (signal.direction != position or
                        (ts - entry_ts).total_seconds() / 3600 >= 72):
                    # Fermer
                    close_price = row.get("close", entry_price)
                    if close_price and entry_price:
                        fill = sim.execute(ts, asset, "sell" if position == "long" else "buy",
                                           0.01, float(close_price))
                        pnl = (float(close_price) - entry_price) * 0.01 if position == "long" else \
                              (entry_price - float(close_price)) * 0.01
                        pnl_net = pnl - fill.fee
                        equity += pnl_net
                        all_trades.append({
                            "timestamp": ts, "asset": asset, "direction": position,
                            "entry_price": entry_price, "exit_price": float(close_price),
                            "pnl_net": pnl_net, "holding_bars": i,
                            "notional": fill.notional,
                        })
                    position = None
                    entry_price = None

            # Ouvrir position si signal actif
            if position is None and signal.direction in ("long", "short"):
                close_price = row.get("close")
                if close_price:
                    fill = sim.execute(ts, asset, "buy" if signal.direction == "long" else "sell",
                                       0.01, float(close_price))
                    if not fill.rejected:
                        position = signal.direction
                        entry_price = fill.price
                        entry_ts = ts

            equity_curve.append({"timestamp": ts, "equity": equity, "asset": asset})

    # Métriques
    if equity_curve:
        equity_df = pd.DataFrame(equity_curve).set_index("timestamp")["equity"]
        trades_df = pd.DataFrame(all_trades)

        try:
            report = compute_equity_metrics(equity_df, trades_df if len(trades_df) > 0 else None)

            if len(trades_df) > 0:
                cost_stress = stress_test_costs(trades_df, args.cost_bps)
                report.pf_cost_x2 = cost_stress.get("pf_cost_x2", 0)
                report.pf_cost_x3 = cost_stress.get("pf_cost_x3", 0)

            metrics = report.to_dict()
            metrics["exec_summary"] = sim.summary()
            metrics["verdict"] = report.verdict()

            # Sauvegarder
            (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
            equity_df.to_frame("equity").to_parquet(out_dir / "equity_curve.parquet")
            if len(trades_df) > 0:
                trades_df.to_parquet(out_dir / "trades.parquet")

            logger.info(f"\n=== RÉSULTATS BACKTEST ===")
            logger.info(f"PF          : {metrics['pf']:.3f}")
            logger.info(f"Sharpe      : {metrics['sharpe']:.3f}")
            logger.info(f"CAGR        : {metrics['cagr']:.2%}")
            logger.info(f"Max DD      : {metrics['max_drawdown']:.2%}")
            logger.info(f"PF cost×2   : {metrics['pf_cost_x2']:.3f}")
            logger.info(f"Worst year  : {metrics['worst_year']:.2%}")
            logger.info(f"N trades    : {metrics['n_trades']}")
            logger.info(f"Verdict     : {metrics['verdict']}")
            logger.info(f"\nRésultats sauvegardés : {out_dir}")

        except Exception as e:
            logger.error(f"Erreur calcul métriques : {e}", exc_info=True)


if __name__ == "__main__":
    main()
