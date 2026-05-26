#!/usr/bin/env python3
"""
Walk-forward SHORT v2.

V2 is a hedge-only research pipeline:
  - multi-asset by construction
  - real derivatives context required
  - real liquidation flow required unless --allow-liquidation-proxy is explicit
  - event-driven labels
  - thresholds optimized on 15 bps + slippage x2 net PnL
  - output is never a live short switch; it is a hedge candidate report
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.level_0.short_features import compute_all_short_features, FEATURES_SHORT_GAMECHANGER
from ai.level_0.short_event_labels_v2 import (
    ACTIVE_COL,
    CONTEXT_COL,
    LABEL_COL,
    build_short_v2_event_labels,
    audit_short_v2_event_labels,
)
from ai.level_0.short_v2_data_contract import (
    ShortV2DataContractError,
    normalize_short_v2_columns,
    validate_short_v2_data_contract,
)
from ai.level_2.short_v2_thresholds import (
    SHORT_V2_EXTREME_COST,
    SHORT_V2_NORMAL_COST,
    SHORT_V2_STRESS_COST,
    backtest_short_v2_thresholds,
    calibrate_short_v2_thresholds,
    classify_short_v2_fold,
    save_thresholds,
)

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score


DATA_DIR = ROOT / "data" / "enriched"
REPORT_DIR = ROOT / "reports" / "short_rebuild_v2"

BASE_LOAD_COLS: Sequence[str] = (
    "datetime",
    "Open", "High", "Low", "Close", "Volume",
    "open", "high", "low", "close", "volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "funding_rate", "funding_rate_z_24", "funding_rate_z_72", "funding_rate_z_288",
    "funding_z_7d", "funding_z_30d", "funding_accel", "funding_extreme",
    "oi_sum", "sum_open_interest", "oi_chg_60m", "oi_chg_240m", "oi_z_1d",
    "oihist_sumOpenInterest_z_24", "oihist_sumOpenInterest_z_72",
    "global_long_short_ratio", "global_ls_longShortRatio_z_24",
    "global_ls_longShortRatio_z_72", "top_trader_lsr", "lsr_z_1d",
    "taker_buy_sell_ratio", "taker_buy_ratio", "taker_ls_buySellRatio_z_24",
    "taker_ls_imbalance",
    "fear_greed_value_z_24", "fear_greed_value_z_72",
    "funding_x_global_ls", "oi_x_fng",
    "global_market_cap_usd_z_24", "btc_dominance_z_24",
    "news_count_z_24", "news_count_z_72",
    "ema_20", "ema_50", "ema_200", "ema_spread_50_200",
    "distance_ema_20", "distance_ema_50", "distance_ema_200",
    "dist_ema_50", "rsi_14", "atr_14", "atr_percent_20", "atr_pct_20",
    "log_return_4", "log_return_12", "log_return_24", "log_return_72",
    "log_return_168", "mom_logret_72",
    "distance_vwap", "distance_vwap_20", "short_term_vwap_distance",
    "realized_volatility_24", "realized_volatility_72",
    "liquidity_score", "local_liquidity_sweep_proxy",
    "liquidity_shock_proxy_20",
    "liq_long_usd", "liq_short_usd",
    "long_liquidation_usd", "short_liquidation_usd",
    "liquidation_long_usd", "liquidation_short_usd",
    "force_order_long_usd", "force_order_short_usd",
    "binance_liq_long_usd", "binance_liq_short_usd",
    "liquidations_long_usd", "liquidations_short_usd",
)

MODEL_FEATURES: Sequence[str] = (
    "funding_rate_z_24", "funding_rate_z_72", "funding_rate_z_288",
    "oihist_sumOpenInterest_z_24", "oihist_sumOpenInterest_z_72",
    "global_ls_longShortRatio_z_24", "global_ls_longShortRatio_z_72",
    "taker_ls_buySellRatio_z_24", "taker_ls_imbalance",
    "fear_greed_value_z_24", "funding_x_global_ls", "oi_x_fng",
    "global_market_cap_usd_z_24", "btc_dominance_z_24",
    "mom_logret_4", "mom_logret_12", "mom_logret_24", "mom_logret_72",
    "ema_spread_50_200", "dist_ema_50", "rsi_14",
    "dist_vwap_pct", "above_vwap_4h", "rv_ratio_24_72",
    "short_v2_macro_bear", "global_bear_breadth",
    *FEATURES_SHORT_GAMECHANGER,
    "short_v2_crowded_longs_score",
    "short_v2_breakdown_score",
    "short_v2_failed_breakout_score",
    "short_v2_liquidity_stress_score",
    "short_v2_bear_continuation_score",
    "short_v2_macro_riskoff_score",
)


def _available_columns(path: Path) -> List[str]:
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).schema.names


def _read_symbol(path: Path) -> pd.DataFrame:
    available = _available_columns(path)
    cols = [col for col in BASE_LOAD_COLS if col in available]
    df = pd.read_parquet(path, columns=cols)
    symbol = path.stem.replace("_1h_enriched", "").upper()
    df["symbol"] = symbol
    df = normalize_short_v2_columns(df)
    df = df.sort_values("datetime").reset_index(drop=True)
    df = compute_all_short_features(df)
    df["short_v2_macro_bear"] = _compute_macro_bear(df)
    return df


def _compute_macro_bear(df: pd.DataFrame) -> np.ndarray:
    close = pd.to_numeric(df["Close"], errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        mom720 = np.log(close / close.shift(720))
    ema200d = close.ewm(span=4800, adjust=False, min_periods=720).mean()
    macro_bear = (close < ema200d) & (mom720 < -0.10)
    fallback = (pd.to_numeric(df["ema_spread_50_200"], errors="coerce") < 0.0) & (
        pd.to_numeric(df["mom_logret_72"], errors="coerce") < 0.0
    )
    return (macro_bear.fillna(False) | fallback.fillna(False)).astype(float).values


def load_short_v2_assets(
    data_dir: Path,
    *,
    max_assets: int,
    require_liquidations: bool,
    allow_liquidation_proxy: bool,
) -> Tuple[pd.DataFrame, List[Dict]]:
    paths = sorted(data_dir.glob("*_1h_enriched.parquet"))
    if not paths:
        raise FileNotFoundError(f"No *_1h_enriched.parquet files found in {data_dir}")

    priority = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "BNBUSDT": 3}

    def key(path: Path) -> Tuple[int, str]:
        sym = path.stem.replace("_1h_enriched", "").upper()
        return priority.get(sym, 100), sym

    paths = sorted(paths, key=key)[:max_assets]
    frames: List[pd.DataFrame] = []
    reports: List[Dict] = []

    for asset_id, path in enumerate(paths):
        print(f"  load {path.name}")
        df = _read_symbol(path)
        df["asset_id"] = asset_id
        report = validate_short_v2_data_contract(
            df,
            require_liquidations=require_liquidations,
            allow_liquidation_proxy=allow_liquidation_proxy,
        )
        reports.append(
            {
                "symbol": df["symbol"].iloc[0],
                "rows": int(len(df)),
                "contract": report.__dict__,
            }
        )
        frames.append(df)

    combined = pd.concat(frames, axis=0, ignore_index=True)
    combined["datetime"] = pd.to_datetime(combined["datetime"], utc=True)

    breadth = (
        combined.assign(dt=combined["datetime"].dt.floor("h"))
        .groupby("dt")["short_v2_macro_bear"]
        .mean()
        .rename("global_bear_breadth")
    )
    combined["global_bear_breadth"] = combined["datetime"].dt.floor("h").map(breadth).fillna(0.0)
    combined = combined.sort_values(["datetime", "asset_id"]).reset_index(drop=True)
    return combined, reports


def _features_available(df: pd.DataFrame) -> List[str]:
    excluded = {
        LABEL_COL,
        "short_v2_gray",
        "short_v2_ret_8h",
        "short_v2_mfe_8h",
        "short_v2_mae_8h",
        "short_v2_net_ret_stress",
        "short_v2_net_ret_normal",
        "future_ret_short_4h",
        "future_ret_short_8h",
        "mfe_short_4h",
        "mae_short_4h",
        "symbol",
        "datetime",
        "short_v2_context",
    }
    features: List[str] = []
    for col in dict.fromkeys(MODEL_FEATURES):
        if col in df.columns and col not in excluded:
            fill = df[col].notna().mean()
            if fill >= 0.60:
                features.append(col)
    return features


def _sample_train_indices(mask: np.ndarray, y: np.ndarray, max_rows: int) -> np.ndarray:
    idx = np.where(mask & (y >= 0))[0]
    if len(idx) <= max_rows:
        return idx
    rng = np.random.default_rng(42)
    pos = idx[y[idx] == 1]
    neg = idx[y[idx] == 0]
    keep_pos = pos
    n_neg = max_rows - len(keep_pos)
    if n_neg <= 0:
        return np.sort(rng.choice(keep_pos, size=max_rows, replace=False))
    keep_neg = rng.choice(neg, size=min(n_neg, len(neg)), replace=False)
    return np.sort(np.concatenate([keep_pos, keep_neg]))


def _fit_model(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    features: List[str],
    *,
    max_train_rows: int,
) -> Tuple[np.ndarray, float]:
    y = df[LABEL_COL].values.astype(np.int8)
    train_idx = _sample_train_indices(train_mask, y, max_train_rows)
    val_idx = np.where(val_mask & (y >= 0))[0]
    if len(train_idx) < 500 or int((y[train_idx] == 1).sum()) < 20:
        raise RuntimeError("not enough train positives for SHORT v2")

    X_train = df.loc[train_idx, features].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y_train = y[train_idx].astype(np.int32)
    X_val = df.loc[val_idx, features].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y_val = y[val_idx].astype(np.int32)

    n_pos = max(int((y_train == 1).sum()), 1)
    n_neg = max(int((y_train == 0).sum()), 1)
    weights = np.where(y_train == 1, n_neg / n_pos, 1.0)

    clf = HistGradientBoostingClassifier(
        max_iter=450,
        learning_rate=0.035,
        max_leaf_nodes=31,
        min_samples_leaf=40,
        l2_regularization=0.10,
        random_state=42,
    )
    clf.fit(X_train, y_train, sample_weight=weights)

    X_all = df[features].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    p_all = clf.predict_proba(X_all)[:, 1].astype(np.float32)

    auc = 0.5
    if len(val_idx) >= 50 and len(np.unique(y_val)) == 2:
        auc = float(roc_auc_score(y_val, p_all[val_idx]))
    return p_all, auc


def run_fold(
    df_base: pd.DataFrame,
    fold_year: int,
    features: List[str],
    *,
    max_train_rows: int,
) -> Dict:
    print(f"\n== fold {fold_year} ==")
    years = df_base["datetime"].dt.year.values
    train_mask = years <= fold_year - 2
    val_mask = years == fold_year - 1
    test_mask = years == fold_year
    if train_mask.sum() < 10_000 or val_mask.sum() < 1_000 or test_mask.sum() < 1_000:
        return {
            "fold_year": fold_year,
            "status": "SKIP",
            "reason": "not_enough_rows",
            "n_train": int(train_mask.sum()),
            "n_val": int(val_mask.sum()),
            "n_test": int(test_mask.sum()),
        }

    df = _build_labels_by_asset(df_base, fold_year)
    label_audit = audit_short_v2_event_labels(df)
    n_pos = int(((df[LABEL_COL].values == 1) & train_mask).sum())
    if n_pos < 20:
        return {
            "fold_year": fold_year,
            "status": "SKIP",
            "reason": "too_few_event_positives",
            "n_pos_train": n_pos,
            "label_audit": label_audit,
        }

    fold_features = [f for f in features if f in df.columns]
    p_all, auc_val = _fit_model(
        df,
        train_mask,
        val_mask,
        fold_features,
        max_train_rows=max_train_rows,
    )

    df_val = df[val_mask].reset_index(drop=True)
    p_val = p_all[val_mask]
    thresholds = calibrate_short_v2_thresholds(df_val, p_val)
    save_thresholds(thresholds, REPORT_DIR / f"thresholds_fold_{fold_year}_v2.json")

    df_test = df[test_mask].reset_index(drop=True)
    p_test = p_all[test_mask]
    normal = backtest_short_v2_thresholds(df_test, p_test, thresholds, cost=SHORT_V2_NORMAL_COST)
    stress = backtest_short_v2_thresholds(df_test, p_test, thresholds, cost=SHORT_V2_STRESS_COST)
    extreme = backtest_short_v2_thresholds(df_test, p_test, thresholds, cost=SHORT_V2_EXTREME_COST)
    fold_class = classify_short_v2_fold(stress)

    print(
        f"  trades={stress['n_trades']} pf_stress={stress['pf']:.3f} "
        f"exp={stress['expectancy']:+.5f} auc={auc_val:.4f} -> {fold_class['fold_status']}"
    )

    return {
        "fold_year": fold_year,
        "status": "RUN",
        **fold_class,
        "auc_val": round(auc_val, 4),
        "n_train": int(train_mask.sum()),
        "n_val": int(val_mask.sum()),
        "n_test": int(test_mask.sum()),
        "n_pos_train": n_pos,
        "n_features": len(fold_features),
        "label_audit": label_audit,
        "normal": normal,
        "stress": stress,
        "extreme": extreme,
        "thresholds": thresholds,
    }


def _build_labels_by_asset(df_base: pd.DataFrame, fold_year: int) -> pd.DataFrame:
    """Build forward labels per asset to avoid cross-symbol future returns."""
    parts: List[pd.DataFrame] = []
    group_col = "asset_id" if "asset_id" in df_base.columns else "symbol"
    for _, group in df_base.groupby(group_col, sort=True):
        group = group.sort_values("datetime").reset_index(drop=True)
        years = group["datetime"].dt.year.values
        group_train_mask = years <= fold_year - 2
        if group_train_mask.sum() < 100:
            parts.append(group)
            continue
        parts.append(build_short_v2_event_labels(group, group_train_mask))
    return pd.concat(parts, axis=0, ignore_index=True).sort_values(["datetime", group_col]).reset_index(drop=True)


def compute_verdict(results: List[Dict]) -> str:
    run = [r for r in results if r.get("status") == "RUN"]
    if not run:
        return "SHORT_V2_REJECTED"
    if any(r.get("fold_catastrophic") for r in run):
        return "SHORT_V2_REJECTED"
    ok = sum(1 for r in run if r.get("fold_ok"))
    total_trades = sum(r["stress"]["n_trades"] for r in run)
    if ok == len(run) and total_trades >= 100:
        return "SHORT_V2_HEDGE_PAPER_CANDIDATE"
    return "SHORT_V2_PROMISING_BUT_UNSAFE"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SHORT v2 hedge-only walk-forward")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--max-assets", type=int, default=50)
    parser.add_argument("--folds", nargs="+", type=int, default=[2022, 2023, 2024, 2025, 2026])
    parser.add_argument("--max-train-rows", type=int, default=180_000)
    parser.add_argument("--allow-liquidation-proxy", action="store_true")
    parser.add_argument("--no-require-liquidations", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("\nSHORT v2 walk-forward")
    print(f"  data_dir: {args.data_dir}")
    print(f"  folds: {args.folds}")
    print(f"  liquidation_proxy: {'ON' if args.allow_liquidation_proxy else 'OFF'}")

    try:
        df, contract_reports = load_short_v2_assets(
            Path(args.data_dir),
            max_assets=args.max_assets,
            require_liquidations=not args.no_require_liquidations,
            allow_liquidation_proxy=args.allow_liquidation_proxy,
        )
    except (FileNotFoundError, ShortV2DataContractError) as exc:
        summary = {
            "verdict": "SHORT_V2_DATA_CONTRACT_FAILED",
            "error": str(exc),
            "run_date": pd.Timestamp.utcnow().isoformat(),
            "deployment_mode": "hedge_only",
            "live_allowed": False,
            "paper_allowed": False,
        }
        path = REPORT_DIR / "walk_forward_short_v2.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        print(f"\nDATA CONTRACT FAILED: {exc}")
        print(f"report: {path}")
        sys.exit(2)

    features = _features_available(df)
    if len(features) < 20:
        raise RuntimeError(f"too few SHORT v2 features available: {len(features)}")
    print(f"  rows={len(df):,} assets={df['symbol'].nunique()} features={len(features)}")

    fold_results: List[Dict] = []
    for fold_year in args.folds:
        try:
            fold_results.append(
                run_fold(
                    df,
                    fold_year,
                    features,
                    max_train_rows=args.max_train_rows,
                )
            )
        except Exception as exc:
            print(f"  fold {fold_year} error: {exc}")
            fold_results.append({"fold_year": fold_year, "status": "ERROR", "error": str(exc)})

    raw_verdict = compute_verdict(fold_results)
    deployment_grade_data = all(
        bool(report["contract"]["present_columns"].get("real_liquidations"))
        and not report["contract"].get("using_liquidation_proxy", False)
        for report in contract_reports
    )
    if not deployment_grade_data and raw_verdict == "SHORT_V2_HEDGE_PAPER_CANDIDATE":
        verdict = "SHORT_V2_RESEARCH_ONLY_DATA_INCOMPLETE"
    else:
        verdict = raw_verdict
    paper_allowed = (
        raw_verdict == "SHORT_V2_HEDGE_PAPER_CANDIDATE"
        and deployment_grade_data
    )
    summary = {
        "verdict": verdict,
        "raw_model_verdict": raw_verdict,
        "run_date": pd.Timestamp.utcnow().isoformat(),
        "deployment_mode": "hedge_only",
        "deployment_grade_data": deployment_grade_data,
        "live_allowed": False,
        "paper_allowed": paper_allowed,
        "cost_objective": {
            "normal_cost": SHORT_V2_NORMAL_COST,
            "stress_cost_15bps_slippage_x2": SHORT_V2_STRESS_COST,
            "extreme_cost": SHORT_V2_EXTREME_COST,
        },
        "data_contract": contract_reports,
        "n_assets": int(df["symbol"].nunique()),
        "features": features,
        "fold_results": fold_results,
        "duration_s": round(time.time() - t0, 1),
    }

    out = REPORT_DIR / "walk_forward_short_v2.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)

    print(f"\nVERDICT: {verdict}")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
