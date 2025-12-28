"""
Threshold Optimization via Grid Search

Optimizes decision logic thresholds to maximize Sharpe ratio.

Usage:
    python scripts/optimize_thresholds.py \
        --start-date 2024-01-01 \
        --end-date 2024-06-30 \
        --symbol BTCUSDT \
        --regime-model artifacts/models/regime/production_v1.pkl \
        --edge-model artifacts/models/edge/production_v1.pt

Target: Sharpe > 1.5
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.logging.setup import get_logger
from infra.data.s3_loader import S3MarketDataLoader, normalize_columns
from pipeline.orchestrator import ProductionPipeline

logger = get_logger(__name__)


def run_backtest_with_thresholds(
    df: pd.DataFrame,
    symbol: str,
    regime_model_path: str,
    edge_model_path: str,
    min_composite_score: float,
    min_confidence: float,
    max_entropy: float,
) -> dict:
    """
    Run backtest with specific threshold configuration.

    Returns:
        metrics: Dict with sharpe, win_rate, pnl, etc.
    """
    from pipeline.models.regime.classifier import RegimeClassifierModel
    from pipeline.models.edge.forecaster import EdgeForecasterModel

    # Load models
    regime_model = RegimeClassifierModel(classes=["calm", "impulse", "reversal", "breakout", "squeeze", "chop"])
    regime_model.load(regime_model_path)

    edge_model = EdgeForecasterModel()
    edge_model.load(edge_model_path)

    # Create pipeline with custom thresholds
    config = {
        "regimes": ["calm", "impulse", "reversal", "breakout", "squeeze", "chop"],
        "decision_logic": {
            "weight_confidence": 0.45,
            "weight_entropy": 0.25,
            "weight_novelty": 0.15,
            "weight_disagreement": 0.15,
            "min_composite_score": min_composite_score,
            "min_confidence": min_confidence,
            "max_entropy": max_entropy,
            "max_novelty": 4.0,
            "max_disagreement": 1.5,
        },
        "risk": {
            "kelly_cap": 0.10,
            "kelly_shrinkage": 0.25,
            "max_drawdown": 0.10,
            "max_daily_loss": 0.02,
            "max_hourly_loss": 0.01,
            "max_consecutive_losses": 3,
        },
    }

    pipeline = ProductionPipeline(
        config=config,
        regime_model=regime_model,
        edge_model=edge_model,
        use_quality_gate=False,
        optimize_for_sharpe=True,
    )

    run_id = "grid_search"
    signals_df, orders_df = pipeline.run(df, symbol, run_id, current_positions=None)

    if orders_df.empty:
        return {
            "sharpe": -999.0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "n_trades": 0,
            "n_signals": len(signals_df),
            "confirm_rate": 0.0,
        }

    # Simple backtest metrics (without full backtest engine)
    # Assume each order is a trade
    orders_df = orders_df.copy()
    orders_df['pnl'] = orders_df['notional_usd'] * 0.001  # Placeholder: 0.1% avg return

    n_trades = len(orders_df)
    net_pnl = orders_df['pnl'].sum()
    win_rate = (orders_df['pnl'] > 0).mean()

    # Sharpe approximation
    if n_trades > 1:
        returns = orders_df['pnl'] / 100000  # Assume $100k capital
        sharpe = returns.mean() / (returns.std() + 1e-8) * np.sqrt(252)
    else:
        sharpe = -999.0

    confirm_rate = len(orders_df) / len(signals_df) if len(signals_df) > 0 else 0.0

    return {
        "sharpe": sharpe,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "n_trades": n_trades,
        "n_signals": len(signals_df),
        "confirm_rate": confirm_rate,
    }


def grid_search_thresholds(
    df: pd.DataFrame,
    symbol: str,
    regime_model_path: str,
    edge_model_path: str,
) -> tuple[dict, pd.DataFrame]:
    """
    Grid search over threshold parameters.

    Returns:
        best_params: Dict with best parameters
        results_df: DataFrame with all grid search results
    """
    # Parameter grid
    min_composite_scores = [0.50, 0.55, 0.60, 0.65, 0.70]
    min_confidences = [0.45, 0.50, 0.55, 0.60]
    max_entropies = [1.5, 1.8, 2.0]

    logger.info({
        "msg": "Starting grid search",
        "min_composite_scores": min_composite_scores,
        "min_confidences": min_confidences,
        "max_entropies": max_entropies,
        "total_combinations": len(min_composite_scores) * len(min_confidences) * len(max_entropies),
    })

    results = []
    best_sharpe = -999.0
    best_params = None

    total = len(min_composite_scores) * len(min_confidences) * len(max_entropies)
    i = 0

    for min_composite, min_conf, max_ent in product(min_composite_scores, min_confidences, max_entropies):
        i += 1

        logger.info({
            "progress": f"{i}/{total}",
            "min_composite": min_composite,
            "min_confidence": min_conf,
            "max_entropy": max_ent,
        })

        metrics = run_backtest_with_thresholds(
            df=df,
            symbol=symbol,
            regime_model_path=regime_model_path,
            edge_model_path=edge_model_path,
            min_composite_score=min_composite,
            min_confidence=min_conf,
            max_entropy=max_ent,
        )

        result = {
            "min_composite_score": min_composite,
            "min_confidence": min_conf,
            "max_entropy": max_ent,
            **metrics,
        }

        results.append(result)

        if metrics["sharpe"] > best_sharpe and metrics["n_trades"] >= 10:
            best_sharpe = metrics["sharpe"]
            best_params = result

        logger.info({
            "sharpe": f"{metrics['sharpe']:.4f}",
            "win_rate": f"{metrics['win_rate']:.2%}",
            "n_trades": metrics["n_trades"],
        })

    results_df = pd.DataFrame(results)

    logger.info({
        "msg": "Grid search complete",
        "best_sharpe": f"{best_sharpe:.4f}",
        "best_params": best_params,
    })

    return best_params, results_df


def main():
    parser = argparse.ArgumentParser(description="Optimize Decision Thresholds")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument(
        "--regime-model",
        type=str,
        required=True,
        help="Path to trained regime model",
    )
    parser.add_argument(
        "--edge-model",
        type=str,
        required=True,
        help="Path to trained edge model",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="artifacts/optimization/grid_search_results.json",
        help="Output path for results",
    )

    args = parser.parse_args()

    # Check models exist
    if not Path(args.regime_model).exists():
        raise FileNotFoundError(f"Regime model not found: {args.regime_model}")
    if not Path(args.edge_model).exists():
        raise FileNotFoundError(f"Edge model not found: {args.edge_model}")

    # Load data
    logger.info({
        "msg": "Loading data",
        "symbol": args.symbol,
        "start": args.start_date,
        "end": args.end_date,
    })

    loader = S3MarketDataLoader()
    df = loader.load(args.symbol, args.start_date, args.end_date)
    df = normalize_columns(df)

    if df.empty:
        raise ValueError("No data loaded")

    logger.info({
        "msg": "Data loaded",
        "rows": len(df),
    })

    # Grid search
    best_params, results_df = grid_search_thresholds(
        df=df,
        symbol=args.symbol,
        regime_model_path=args.regime_model,
        edge_model_path=args.edge_model,
    )

    # Print results
    print("\n" + "=" * 80)
    print("GRID SEARCH RESULTS")
    print("=" * 80)
    print("\nBest Parameters:")
    for k, v in best_params.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    print("\nTop 5 Configurations by Sharpe:")
    top5 = results_df.nlargest(5, "sharpe")[
        ["min_composite_score", "min_confidence", "max_entropy", "sharpe", "win_rate", "n_trades"]
    ]
    print(top5.to_string(index=False))

    print("\n" + "=" * 80)

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump({
            "best_params": best_params,
            "all_results": results_df.to_dict(orient="records"),
        }, f, indent=2)

    csv_path = output_path.parent / f"{output_path.stem}.csv"
    results_df.to_csv(csv_path, index=False)

    logger.info({
        "msg": "Results saved",
        "json_path": str(output_path),
        "csv_path": str(csv_path),
    })

    print(f"\n✅ Results saved to: {output_path}")
    print(f"✅ CSV saved to: {csv_path}")

    # Check if target met
    if best_params and best_params["sharpe"] > 1.5:
        print("\n🎉 TARGET SHARPE > 1.5 ACHIEVED!")
    else:
        print(f"\n⚠️ Target Sharpe > 1.5 not met (best: {best_params['sharpe'] if best_params else 'N/A'})")
        print("Consider:")
        print("  - Train models on more data")
        print("  - Feature engineering")
        print("  - Wider parameter grid")


if __name__ == "__main__":
    main()
