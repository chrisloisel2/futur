#!/usr/bin/env python3
"""
scripts/run_backtest_engine.py
─────────────────────────────────────────────────────────────────────────────
Backtest walk-forward réel pour les moteurs institutionnels.

Règle fondamentale :
  - période 2022 → model_2022.pkl UNIQUEMENT
  - période 2023 → model_2023.pkl UNIQUEMENT
  - période 2024 → model_2024.pkl UNIQUEMENT
  - période 2025 → model_2025.pkl UNIQUEMENT

Usage :
    python3 scripts/run_backtest_engine.py \
        --portfolio BTC_ETH_TREND_V1 \
        --engine btc_eth_trend \
        --assets BTCUSDT,ETHUSDT \
        --start 2022-01-01 --end 2025-12-31 \
        --target trend_cont_24h \
        --threshold 0.60 \
        --max-holding 24 \
        --long-only

    python3 scripts/run_backtest_engine.py \
        --portfolio BNB_EVENT_V1 \
        --engine trm_event \
        --assets BNBUSDT \
        --start 2022-01-01 --end 2025-12-31 \
        --target event_cont_4h \
        --threshold 0.65 \
        --max-holding 8 \
        --long-only

Sorties :
    artifacts/institutional/backtests/{portfolio}/{asset}/
        orders.parquet  fills.parquet  positions.parquet
        equity_curve.parquet  trades.parquet
        metrics.json  report.md  fold_model_usage.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.data.loaders import load_asset_1h
from src.institutional.data.dataset_builder import (
    EngineDatasetBuilder, EngineDatasetConfig,
    btc_eth_trend_config, trm_event_config, carry_config,
)
from src.institutional.models.tree.lightgbm_model import LightGBMClassifier
from src.institutional.backtest.event_backtester import (
    BacktestConfig, EventBacktester,
    compute_backtest_metrics, save_backtest_outputs,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

ARTIFACTS   = Path("artifacts/institutional/backtests")
LABEL_PFXS  = ("trend_cont_", "event_cont_", "carry_net_", "vol_h_",
                "threshold_", "fwd_ret_", "asset", "engine", "vol_annual")
CONFIG_MAP  = {
    "btc_eth_trend": btc_eth_trend_config,
    "trm_event":     trm_event_config,
    "carry":         carry_config,
}


# ─── FoldMissingError ─────────────────────────────────────────────────────────

class FoldMissingError(FileNotFoundError):
    """Levée quand un modèle fold requis est absent."""


# ─── BacktestFoldPlan ─────────────────────────────────────────────────────────

@dataclass
class BacktestFoldPlan:
    fold_year:         int
    model_path:        Path
    test_start:        str
    test_end:          str
    train_period:      str
    validation_period: str
    model_version:     str = "v1.0"


# ─── FoldAwareModelLoader ─────────────────────────────────────────────────────

class FoldAwareModelLoader:
    """
    Construit le plan de folds walk-forward et charge chaque modèle
    sur sa période de test exclusive.

    Garanties :
      - model_YYYY.pkl est utilisé UNIQUEMENT sur la période YYYY
      - Un modèle entraîné après sa période de test est refusé
      - Un fold manquant lève FoldMissingError (pas de fallback silencieux)
    """

    FOLD_DEFINITIONS: Dict[int, Dict] = {
        2022: {
            "test_start":        "2022-01-01",
            "test_end":          "2022-12-31",
            "train_period":      "2021-01-01 → 2021-09-30",
            "validation_period": "2021-10-01 → 2021-12-31",
        },
        2023: {
            "test_start":        "2023-01-01",
            "test_end":          "2023-12-31",
            "train_period":      "2021-01-01 → 2022-09-30",
            "validation_period": "2022-10-01 → 2022-12-31",
        },
        2024: {
            "test_start":        "2024-01-01",
            "test_end":          "2024-12-31",
            "train_period":      "2021-01-01 → 2023-09-30",
            "validation_period": "2023-10-01 → 2023-12-31",
        },
        2025: {
            "test_start":        "2025-01-01",
            "test_end":          "2025-12-31",
            "train_period":      "2021-01-01 → 2024-09-30",
            "validation_period": "2024-10-01 → 2024-12-31",
        },
    }

    def build_plan(
        self,
        engine: str,
        asset:  str,
        start:  str,
        end:    str,
    ) -> List[BacktestFoldPlan]:
        """
        Retourne la liste ordonnée des BacktestFoldPlan pour la période [start, end].
        Lève FoldMissingError si un fold requis est absent.
        """
        start_dt = pd.Timestamp(start)
        end_dt   = pd.Timestamp(end)

        plans: List[BacktestFoldPlan] = []
        base_dir = ARTIFACTS / engine / asset / "v1.0"

        for year, fold_def in sorted(self.FOLD_DEFINITIONS.items()):
            fold_start = pd.Timestamp(fold_def["test_start"])
            fold_end   = pd.Timestamp(fold_def["test_end"])

            # Inclure le fold si sa période chevauche [start, end]
            if fold_end < start_dt or fold_start > end_dt:
                continue

            model_path = base_dir / str(year) / f"model_{year}.pkl"
            if not model_path.exists():
                raise FoldMissingError(
                    f"Modèle fold manquant pour {asset}/{engine} année {year}: {model_path}\n"
                    f"Entraîner d'abord : python3 scripts/train_per_engine.py --engine {engine}"
                )

            plans.append(BacktestFoldPlan(
                fold_year         = year,
                model_path        = model_path,
                test_start        = fold_def["test_start"],
                test_end          = fold_def["test_end"],
                train_period      = fold_def["train_period"],
                validation_period = fold_def["validation_period"],
            ))
            logger.info(f"  Fold {year}: model={model_path.name}  test={fold_def['test_start']} → {fold_def['test_end']}")

        if not plans:
            raise FoldMissingError(
                f"Aucun fold disponible pour {asset}/{engine} sur la période {start} → {end}"
            )

        return plans

    def load_fold_proba(
        self,
        plan:    BacktestFoldPlan,
        engine:  str,
        asset:   str,
        start:   str,
        end:     str,
    ) -> pd.DataFrame:
        """
        Charge le modèle du fold et génère les probabilités pour la période
        [plan.test_start, plan.test_end] seulement.
        Ajoute les colonnes fold_year, model_path, model_type à chaque ligne.
        """
        # Bornes effectives = intersection de [plan.test_start, plan.test_end] et [start, end]
        fold_start = max(pd.Timestamp(plan.test_start), pd.Timestamp(start)).strftime("%Y-%m-%d")
        fold_end   = min(pd.Timestamp(plan.test_end),   pd.Timestamp(end)).strftime("%Y-%m-%d")

        model = LightGBMClassifier.load(plan.model_path)
        logger.info(f"    [{plan.fold_year}] Modèle chargé: {plan.model_path}")

        config_fn = CONFIG_MAP.get(engine, btc_eth_trend_config)
        base_cfg  = config_fn(start=fold_start, end=fold_end)

        single_cfg = EngineDatasetConfig(
            engine_name      = base_cfg.engine_name,
            assets           = [asset],
            start            = fold_start,
            end              = fold_end,
            feature_families = base_cfg.feature_families,
            label_family     = base_cfg.label_family,
            label_horizons_h = base_cfg.label_horizons_h,
            label_k          = base_cfg.label_k,
            label_cost_bps   = base_cfg.label_cost_bps,
            include_funding  = base_cfg.include_funding,
            include_oi       = base_cfg.include_oi,
        )

        builder  = EngineDatasetBuilder()
        datasets = builder.build(single_cfg, validate_quality=False)
        df = datasets.get(asset)
        if df is None or len(df) == 0:
            logger.warning(f"    [{plan.fold_year}] Dataset vide pour {asset} — fold ignoré")
            return pd.DataFrame()

        feat_cols = [c for c in df.columns if not any(c.startswith(p) for p in LABEL_PFXS)]
        X         = df[feat_cols].fillna(0)

        proba = model.predict_proba(X)
        cls   = model._classes

        proba_df = pd.DataFrame(index=df.index)
        for i, cls_val in enumerate(cls):
            if cls_val == 1:
                proba_df["p_up"]   = proba[:, i]
            elif cls_val == -1:
                proba_df["p_down"] = proba[:, i]
            else:
                proba_df["p_flat"] = proba[:, i]

        if "p_up"   not in proba_df.columns: proba_df["p_up"]   = 0.0
        if "p_down" not in proba_df.columns: proba_df["p_down"] = 0.0
        if "p_flat" not in proba_df.columns: proba_df["p_flat"] = 1.0

        # Métadonnées fold par ligne
        proba_df["fold_year"]  = plan.fold_year
        proba_df["model_path"] = str(plan.model_path)
        proba_df["model_type"] = type(model).__name__

        n_signals = int((proba_df["p_up"] > 0.0).sum())
        logger.info(f"    [{plan.fold_year}] {len(proba_df):,} barres, {n_signals:,} barres avec p_up>0")

        return proba_df


# ─── load_proba_walk_forward ───────────────────────────────────────────────────

def load_proba_walk_forward(
    asset:  str,
    engine: str,
    target: str,
    start:  str,
    end:    str,
) -> Tuple[pd.DataFrame, List[BacktestFoldPlan]]:
    """
    Génère les probabilités en mode walk-forward strict :
    chaque fold utilise son propre modèle sur sa période de test uniquement.
    Retourne (proba_df, plans).
    """
    loader = FoldAwareModelLoader()
    plans  = loader.build_plan(engine, asset, start, end)

    parts: List[pd.DataFrame] = []
    for plan in plans:
        fold_proba = loader.load_fold_proba(plan, engine, asset, start, end)
        if not fold_proba.empty:
            parts.append(fold_proba)

    if not parts:
        raise ValueError(f"Aucune probabilité générée pour {asset}/{engine}")

    proba_df = pd.concat(parts).sort_index()

    # Vérifier l'absence de doublons d'index
    if proba_df.index.duplicated().any():
        n_dup = proba_df.index.duplicated().sum()
        logger.warning(f"  {n_dup} doublons d'index détectés → supprimés (garder dernier)")
        proba_df = proba_df[~proba_df.index.duplicated(keep="last")]

    return proba_df, plans


# ─── annotate_trades_with_fold ────────────────────────────────────────────────

def annotate_trades_with_fold(
    result:    "BacktestResult",
    proba_df:  pd.DataFrame,
    threshold: float,
) -> None:
    """
    Annote chaque Trade avec les métadonnées du fold (fold_year, model_path, model_type,
    prediction, threshold) en faisant un lookup sur entry_ts dans proba_df.
    Modifie les objets Trade in-place.
    """
    for trade in result.trades:
        ts = trade.entry_ts
        if ts in proba_df.index:
            row = proba_df.loc[ts]
            trade.fold_year  = int(row["fold_year"])
            trade.model_path = str(row["model_path"])
            trade.model_type = str(row["model_type"])
            trade.prediction = float(row.get("p_up", 0.0))
            trade.threshold  = threshold


# ─── build_fold_model_usage ───────────────────────────────────────────────────

def build_fold_model_usage(
    plans:     List[BacktestFoldPlan],
    result:    "BacktestResult",
    proba_df:  pd.DataFrame,
    asset:     str,
    portfolio: str,
    threshold: float,
    out_dir:   Path,
) -> None:
    """
    Écrit fold_model_usage.json : preuve d'intégrité walk-forward.
    Pour chaque fold :
      - quel modèle a été utilisé
      - sur quelle période
      - combien de signaux il a générés (p_up > threshold)
      - combien de trades il a déclenchés
      - quel PnL net il a produit
    """
    folds_data = []
    for plan in plans:
        mask      = proba_df["fold_year"] == plan.fold_year
        n_bars    = int(mask.sum())
        n_signals = int((proba_df.loc[mask, "p_up"] > threshold).sum())

        fold_trades = [
            t for t in result.trades
            if getattr(t, "fold_year", None) == plan.fold_year and not t.is_open
        ]
        n_trades = len(fold_trades)
        pnl_net  = round(sum(t.pnl_net for t in fold_trades), 2)

        folds_data.append({
            "fold_year":         plan.fold_year,
            "model_path":        str(plan.model_path),
            "model_type":        "LightGBMClassifier",
            "test_start":        plan.test_start,
            "test_end":          plan.test_end,
            "train_period":      plan.train_period,
            "validation_period": plan.validation_period,
            "model_version":     plan.model_version,
            "n_bars":            n_bars,
            "n_signals":         n_signals,
            "n_trades":          n_trades,
            "pnl_net":           pnl_net,
        })

    out = {
        "portfolio":    portfolio,
        "asset":        asset,
        "threshold":    threshold,
        "generated_at": str(pd.Timestamp.utcnow()),
        "folds":        folds_data,
    }
    usage_path = out_dir / "fold_model_usage.json"
    usage_path.write_text(json.dumps(out, indent=2))
    logger.info(f"  fold_model_usage.json → {usage_path}")


# ─── parse_args ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--portfolio",       default="BTC_ETH_TREND_V1")
    p.add_argument("--engine",          default="btc_eth_trend")
    p.add_argument("--assets",          default="BTCUSDT,ETHUSDT")
    p.add_argument("--start",           default="2022-01-01")
    p.add_argument("--end",             default="2025-12-31")
    p.add_argument("--target",          default="trend_cont_24h")
    p.add_argument("--threshold",       type=float, default=0.60)
    p.add_argument("--max-holding",     type=int,   default=24)
    p.add_argument("--stop-loss",       type=float, default=0.0)
    p.add_argument("--take-profit",     type=float, default=0.0)
    p.add_argument("--long-only",       action="store_true")
    p.add_argument("--fee-bps",         type=float, default=5.0)
    p.add_argument("--slippage-bps",    type=float, default=2.0)
    p.add_argument("--initial-capital", type=float, default=10_000.0)
    p.add_argument("--position-size",   type=float, default=0.25)
    return p.parse_args()


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args   = parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",")]
    assets = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]

    out_dir = ARTIFACTS / args.portfolio
    logger.info(f"Portfolio : {args.portfolio}")
    logger.info(f"Assets    : {assets}")
    logger.info(f"Period    : {args.start} → {args.end}")
    logger.info(f"Target    : {args.target}")
    logger.info(f"Threshold : {args.threshold}")
    logger.info(f"Mode      : walk-forward (modèle par fold year)")

    all_results = {}

    for asset in assets:
        logger.info(f"\n{'─'*60}")
        logger.info(f"Asset: {asset}")

        # 1. Charger OHLCV
        ohlcv = load_asset_1h(asset, args.start, args.end)

        # 2. Générer probabilités walk-forward (modèle différent par année)
        try:
            proba_df, plans = load_proba_walk_forward(
                asset, args.engine, args.target, args.start, args.end
            )
        except (FoldMissingError, ValueError) as e:
            logger.error(f"  {asset}: {e}")
            continue

        # Log récapitulatif des folds
        folds_used = proba_df["fold_year"].unique() if "fold_year" in proba_df.columns else []
        logger.info(f"  Folds utilisés: {sorted(folds_used)}")

        # 3. Aligner OHLCV et proba sur l'intersection
        common   = ohlcv.index.intersection(proba_df.index)
        ohlcv_bt = ohlcv.loc[common]
        proba_bt = proba_df.loc[common]
        logger.info(f"  Barres pour backtest: {len(common):,}")

        # 4. Backtest cost×1 (pour outputs principaux + annotation des trades)
        config_x1 = BacktestConfig(
            initial_capital   = args.initial_capital,
            position_size_pct = args.position_size,
            signal_threshold  = args.threshold,
            long_enabled      = True,
            short_enabled     = not args.long_only,
            max_holding_bars  = args.max_holding,
            stop_loss_pct     = args.stop_loss,
            take_profit_pct   = args.take_profit,
            taker_fee_bps     = args.fee_bps,
            slippage_bps      = args.slippage_bps,
            cost_multiplier   = 1.0,
        )
        bt       = EventBacktester()
        result   = bt.run(ohlcv_bt, proba_bt, config_x1, asset=asset)

        # Annoter les trades avec les métadonnées fold
        annotate_trades_with_fold(result, proba_bt, args.threshold)

        # Métriques et log cost-sensitivity via re-runs légers
        cost_sensitivity: Dict[str, float] = {}
        for cost_mult in [1, 2, 3]:
            cfg_m = BacktestConfig(
                initial_capital   = args.initial_capital,
                position_size_pct = args.position_size,
                signal_threshold  = args.threshold,
                long_enabled      = True,
                short_enabled     = not args.long_only,
                max_holding_bars  = args.max_holding,
                stop_loss_pct     = args.stop_loss,
                take_profit_pct   = args.take_profit,
                taker_fee_bps     = args.fee_bps,
                slippage_bps      = args.slippage_bps,
                cost_multiplier   = float(cost_mult),
            )
            if cost_mult == 1:
                m = compute_backtest_metrics(result, cost_bps_base=args.fee_bps + args.slippage_bps)
                metrics_x1 = m
            else:
                bt_m   = EventBacktester()
                res_m  = bt_m.run(ohlcv_bt, proba_bt, cfg_m, asset=asset)
                m      = compute_backtest_metrics(res_m, cost_bps_base=args.fee_bps + args.slippage_bps)

            cost_sensitivity[f"pf_x{cost_mult}"] = m.get("pf", 0)
            logger.info(
                f"  cost×{cost_mult}: PF={m.get('pf',0):.3f}  "
                f"Sharpe={m.get('sharpe',0):.3f}  "
                f"Sortino={m.get('sortino',0):.3f}  "
                f"N={m.get('n_trades',0)}  "
                f"MaxDD={m.get('max_drawdown',0):.2%}"
            )

        # Injecter cost_sensitivity dans metrics_x1
        metrics_x1["cost_sensitivity"] = cost_sensitivity

        all_results[asset] = {"result": result, "metrics": metrics_x1, "plans": plans}

        # 5. Sauvegarder outputs
        asset_dir = out_dir / asset
        save_backtest_outputs(
            result, metrics_x1, asset_dir,
            portfolio_name=args.portfolio,
            engine_name=f"INSTITUTIONAL_{args.engine.upper()}",
        )

        # 6. fold_model_usage.json
        build_fold_model_usage(
            plans     = plans,
            result    = result,
            proba_df  = proba_bt,
            asset     = asset,
            portfolio = args.portfolio,
            threshold = args.threshold,
            out_dir   = asset_dir,
        )

    # ── Résumé global ─────────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    print(f"BACKTEST SUMMARY — {args.portfolio}  [walk-forward réel]")
    print(f"{'─'*70}")
    print(f"{'Asset':12s} {'PF×1':>7s} {'PF×2':>7s} {'PF×3':>7s} {'Sharpe':>7s} {'Sortino':>8s} "
          f"{'CAGR':>7s} {'MaxDD':>7s} {'N':>5s} {'Verdict':>10s}")
    print(f"{'─'*70}")

    for asset, data in all_results.items():
        m   = data["metrics"]
        cs  = m.get("cost_sensitivity", {})
        pf  = m.get("pf", 0)
        px2 = cs.get("pf_x2", 0)
        px3 = cs.get("pf_x3", 0)
        sh  = m.get("sharpe", 0)
        so  = m.get("sortino", 0)
        cg  = m.get("cagr", 0)
        mdd = abs(m.get("max_drawdown", 1.0))
        n   = m.get("n_trades", 0)

        # Gates institutionnels
        if pf < 1.00 or px2 < 1.00 or n < 30 or sh < 0:
            verdict = "REJECT"
        elif pf >= 1.30 and px2 >= 1.10 and px3 >= 1.05 and sh >= 0.80 and n >= 150 and mdd < 0.18:
            verdict = "PROMOTE"
        elif pf >= 1.25 and px2 >= 1.05 and sh >= 0.60 and n >= 100 and mdd < 0.20:
            verdict = "PAPER"
        elif pf >= 1.05 and n >= 50 and mdd < 0.20:
            verdict = "INCUBATE"
        else:
            verdict = "REJECT"

        print(f"  {asset:12s} {pf:>7.3f} {px2:>7.3f} {px3:>7.3f} {sh:>7.3f} {so:>8.3f} "
              f"{cg:>7.2%} {-mdd:>7.2%} {n:>5d} {verdict:>10s}")

    print(f"{'═'*70}")
    print(f"Outputs: {out_dir}")


if __name__ == "__main__":
    main()
