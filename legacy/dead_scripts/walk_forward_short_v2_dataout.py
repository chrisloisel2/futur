#!/usr/bin/env python3
"""
SHORT v2 walk-forward directly from data_out/result yearly 1m features.

This runner resamples the yearly 1m feature parquets to 1h, trains the latest
SHORT v2 event-driven model, logs OOS trades, and writes monthly performance
tables across all test years.
"""
from __future__ import annotations

import argparse
import json
import re
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
    MAE_COL,
    MFE_COL,
    NO_SHORT_COL,
    RET_COL,
    SHORT_V2_DEFAULT_HORIZONS,
    build_short_v2_event_labels,
    audit_short_v2_event_labels,
    short_v2_exit_params,
)
from ai.level_0.short_v2_data_contract import normalize_short_v2_columns, validate_short_v2_data_contract
from ai.level_2.short_v2_thresholds import (
    SHORT_V2_EXTREME_COST,
    SHORT_V2_NORMAL_COST,
    SHORT_V2_STRESS_COST,
    backtest_short_v2_thresholds,
    calibrate_short_v2_thresholds,
    classify_short_v2_fold,
    collect_short_v2_trades,
    save_thresholds,
)

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score


DATAOUT_DIR = ROOT / "data_out" / "result"
REPORT_DIR = ROOT / "reports" / "short_rebuild_v2_dataout"

LOAD_COLS: Sequence[str] = (
    "timestamp",
    "open", "high", "low", "close", "volume", "quote_volume", "n_trades",
    "taker_buy_base", "funding_rate", "funding_mark_price", "spot_close",
    "oi_sum", "oi_value_sum", "top_trader_lsr", "top_trader_lsr_sum",
    "global_long_short_ratio", "taker_buy_sell_ratio", "fear_greed",
    "ret_240m", "ret_480m", "ret_720m", "ret_1440m",
    "atr_14", "atr_pct_14", "atr_60", "atr_pct_60", "atr_240", "atr_pct_240",
    "ema_dist_8", "ema_dist_21", "ema_dist_55", "ema_dist_144", "ema_dist_288",
    "ema_dist_576", "rsi_14", "rsi_60", "volume_z_60m", "volume_z_240m",
    "vwap_dist_60m", "vwap_dist_240m", "vwap_dist_1440m",
    "taker_buy_ratio", "taker_buy_ratio_z_1h", "taker_buy_ratio_z_4h",
    "funding_z_7d", "funding_z_30d", "funding_accel", "funding_sign",
    "funding_extreme", "oi_chg_60m", "oi_chg_240m", "oi_chg_1440m",
    "oi_z_1d", "oi_price_div_1h", "lsr_z_1d", "lsr_extreme_long",
    "lsr_extreme_short", "taker_ratio_z_1d", "fear_greed_z_30d",
    "top_trader_z_1d", "top_trader_z_7d", "smart_retail_divergence",
    "funding_ma_3d", "funding_ma_7d", "funding_vol_7d", "funding_skew_7d",
    "oi_accel_1h", "oi_accel_z_1d",
)

MODEL_FEATURES: Sequence[str] = (
    "funding_rate_z_24", "funding_rate_z_72", "funding_rate_z_288",
    "oihist_sumOpenInterest_z_24", "oihist_sumOpenInterest_z_72",
    "global_ls_longShortRatio_z_24", "global_ls_longShortRatio_z_72",
    "taker_ls_buySellRatio_z_24", "taker_ls_imbalance",
    "fear_greed_value_z_24", "funding_x_global_ls", "oi_x_fng",
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


def _schema_cols(path: Path) -> list[str]:
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).schema.names


def _parse_file(path: Path) -> tuple[int, str] | None:
    match = re.match(r"^(\d{4})_([A-Za-z0-9]+)_features\.parquet$", path.name)
    if not match:
        return None
    year = int(match.group(1))
    symbol = match.group(2).upper()
    if not symbol.endswith("USDT"):
        return None
    return year, symbol


def _resample_year(path: Path) -> pd.DataFrame:
    cols = [c for c in LOAD_COLS if c in _schema_cols(path)]
    df = pd.read_parquet(path, columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    agg = {}
    for col, rule in {
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "quote_volume": "sum", "n_trades": "sum",
        "taker_buy_base": "sum",
    }.items():
        if col in df.columns:
            agg[col] = rule

    base = df[[c for c in agg]].resample("1h").agg(agg)
    macro_cols = [c for c in df.columns if c not in agg]
    macro_cols = [c for c in macro_cols if c != "timestamp"]
    if macro_cols:
        macro = df[macro_cols].resample("1h").last().ffill(limit=24)
        base = base.join(macro, how="left")

    base = base.dropna(subset=["close"]).reset_index().rename(columns={"timestamp": "datetime"})
    return base


def _add_aliases(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    alias = {
        "Volume": "volume",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "number_of_trades": "n_trades",
        "funding_rate_z_24": "funding_z_7d",
        "funding_rate_z_72": "funding_z_30d",
        "funding_rate_z_288": "funding_z_30d",
        "oihist_sumOpenInterest_z_24": "oi_z_1d",
        "oihist_sumOpenInterest_z_72": "oi_accel_z_1d",
        "global_ls_longShortRatio_z_24": "lsr_z_1d",
        "global_ls_longShortRatio_z_72": "top_trader_z_7d",
        "taker_ls_buySellRatio_z_24": "taker_ratio_z_1d",
        "fear_greed_value_z_24": "fear_greed_z_30d",
        "fear_greed_value_z_72": "fear_greed_z_30d",
        "dist_vwap_pct": "vwap_dist_240m",
        "atr_14": "atr_14",
    }
    for target, source in alias.items():
        if target not in df.columns and source in df.columns:
            df[target] = pd.to_numeric(df[source], errors="coerce")

    if "taker_ls_imbalance" not in df.columns:
        if "taker_buy_ratio" in df.columns:
            df["taker_ls_imbalance"] = (pd.to_numeric(df["taker_buy_ratio"], errors="coerce") - 0.5) * 2.0
        elif "taker_buy_sell_ratio" in df.columns:
            ratio = pd.to_numeric(df["taker_buy_sell_ratio"], errors="coerce")
            df["taker_ls_imbalance"] = (ratio - 1.0) / (ratio + 1.0).clip(lower=1e-9)

    if "funding_x_global_ls" not in df.columns:
        df["funding_x_global_ls"] = (
            pd.to_numeric(df.get("funding_rate_z_24", 0.0), errors="coerce").fillna(0.0)
            * pd.to_numeric(df.get("global_ls_longShortRatio_z_24", 0.0), errors="coerce").fillna(0.0)
        )
    if "oi_x_fng" not in df.columns:
        df["oi_x_fng"] = (
            pd.to_numeric(df.get("oihist_sumOpenInterest_z_24", 0.0), errors="coerce").fillna(0.0)
            * pd.to_numeric(df.get("fear_greed_value_z_24", 0.0), errors="coerce").fillna(0.0)
        )
    if "ema_spread_50_200" not in df.columns:
        d55 = pd.to_numeric(df.get("ema_dist_55", np.nan), errors="coerce")
        d288 = pd.to_numeric(df.get("ema_dist_288", np.nan), errors="coerce")
        df["ema_spread_50_200"] = d288 - d55
    if "dist_ema_50" not in df.columns and "ema_dist_55" in df.columns:
        df["dist_ema_50"] = pd.to_numeric(df["ema_dist_55"], errors="coerce")

    return normalize_short_v2_columns(df)


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


def load_dataout_assets(
    data_dir: Path,
    symbols: list[str] | None,
    max_assets: int,
    allow_liquidation_proxy: bool,
) -> tuple[pd.DataFrame, list[dict]]:
    files: Dict[str, list[Path]] = {}
    for path in sorted(data_dir.glob("*_features.parquet")):
        parsed = _parse_file(path)
        if not parsed:
            continue
        _, symbol = parsed
        files.setdefault(symbol, []).append(path)

    priority = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "XRPUSDT", "DOGEUSDT", "POLUSDT"]
    selected = [s for s in priority if s in files]
    selected += sorted(s for s in files if s not in selected)
    if symbols:
        wanted = {s.upper() for s in symbols}
        selected = [s for s in selected if s in wanted]
    selected = selected[:max_assets]
    if not selected:
        raise FileNotFoundError(f"No usable *_features.parquet symbols in {data_dir}")

    frames: list[pd.DataFrame] = []
    reports: list[dict] = []
    for asset_id, symbol in enumerate(selected):
        print(f"  [{asset_id+1}/{len(selected)}] {symbol}")
        yearly = []
        for path in sorted(files[symbol]):
            yearly.append(_resample_year(path))
        df = pd.concat(yearly, axis=0, ignore_index=True).drop_duplicates("datetime").sort_values("datetime")
        df = _add_aliases(df)
        df = compute_all_short_features(df)
        df["short_v2_macro_bear"] = _compute_macro_bear(df)
        df["symbol"] = symbol
        df["asset_id"] = asset_id
        report = validate_short_v2_data_contract(
            df,
            require_liquidations=not allow_liquidation_proxy,
            allow_liquidation_proxy=allow_liquidation_proxy,
        )
        reports.append({"symbol": symbol, "rows": int(len(df)), "contract": report.__dict__})
        frames.append(df)
        print(f"      rows={len(df):,} {df['datetime'].min().date()} -> {df['datetime'].max().date()} | {report.message}")

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


def _features_available(df: pd.DataFrame) -> list[str]:
    excluded = {
        LABEL_COL, "short_v2_gray", RET_COL, MFE_COL, MAE_COL,
        "short_v2_net_ret_stress", "short_v2_net_ret_normal",
        "symbol", "datetime", "short_v2_context",
    }
    out = []
    for col in dict.fromkeys(MODEL_FEATURES):
        if col in df.columns and col not in excluded and df[col].notna().mean() >= 0.50:
            out.append(col)
    return out


def _sample_train_indices(mask: np.ndarray, y: np.ndarray, max_rows: int) -> np.ndarray:
    idx = np.where(mask & (y >= 0))[0]
    if len(idx) <= max_rows:
        return idx
    rng = np.random.default_rng(42)
    pos = idx[y[idx] == 1]
    neg = idx[y[idx] == 0]
    n_neg = max_rows - len(pos)
    if n_neg <= 0:
        return np.sort(rng.choice(pos, size=max_rows, replace=False))
    return np.sort(np.concatenate([pos, rng.choice(neg, size=min(n_neg, len(neg)), replace=False)]))


def fit_predict(df: pd.DataFrame, train_mask: np.ndarray, val_mask: np.ndarray, features: list[str], max_train_rows: int) -> tuple[np.ndarray, float]:
    y = df[LABEL_COL].values.astype(np.int8)
    train_idx = _sample_train_indices(train_mask, y, max_train_rows)
    val_idx = np.where(val_mask & (y >= 0))[0]
    if len(train_idx) < 500 or int((y[train_idx] == 1).sum()) < 20:
        raise RuntimeError("not enough train positives")
    X_train = df.loc[train_idx, features].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y_train = y[train_idx].astype(np.int32)
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
    p = clf.predict_proba(X_all)[:, 1].astype(np.float32)
    auc = 0.5
    if len(val_idx) > 50 and len(np.unique(y[val_idx])) == 2:
        auc = float(roc_auc_score(y[val_idx], p[val_idx]))
    return p, auc


def run_fold(
    df_base: pd.DataFrame,
    fold_year: int,
    features: list[str],
    max_train_rows: int,
    horizon: int,
) -> dict:
    print(f"\n== fold {fold_year} | horizon {horizon}h ==")
    years = df_base["datetime"].dt.year.values
    train_mask = years <= fold_year - 2
    val_mask = years == fold_year - 1
    test_mask = years == fold_year
    if train_mask.sum() < 10_000 or val_mask.sum() < 1_000 or test_mask.sum() < 1_000:
        return {"fold_year": fold_year, "horizon": horizon, "status": "SKIP", "reason": "not_enough_rows"}

    df = _build_labels_by_asset(df_base, fold_year, horizon)
    label_audit = audit_short_v2_event_labels(df)
    n_pos_train = int(((df[LABEL_COL].values == 1) & train_mask).sum())
    p_all, auc_val = fit_predict(df, train_mask, val_mask, features, max_train_rows)

    df_val = df[val_mask].reset_index(drop=True)
    thresholds = calibrate_short_v2_thresholds(df_val, p_all[val_mask])
    save_thresholds(thresholds, REPORT_DIR / f"thresholds_fold_{fold_year}_h{horizon}_dataout_v2.json")

    df_test = df[test_mask].reset_index(drop=True)
    p_test = p_all[test_mask]
    normal = backtest_short_v2_thresholds(df_test, p_test, thresholds, cost=SHORT_V2_NORMAL_COST)
    stress = backtest_short_v2_thresholds(df_test, p_test, thresholds, cost=SHORT_V2_STRESS_COST)
    extreme = backtest_short_v2_thresholds(df_test, p_test, thresholds, cost=SHORT_V2_EXTREME_COST)
    trades = collect_short_v2_trades(df_test, p_test, thresholds, cost=SHORT_V2_STRESS_COST, horizon=horizon)
    fold_class = classify_short_v2_fold(stress)
    print(f"  h={horizon} trades={stress['n_trades']} pf_stress={stress['pf']:.3f} exp={stress['expectancy']:+.5f} auc={auc_val:.4f} -> {fold_class['fold_status']}")
    return {
        "fold_year": fold_year,
        "horizon": horizon,
        "status": "RUN",
        **fold_class,
        "auc_val": round(auc_val, 4),
        "n_train": int(train_mask.sum()),
        "n_val": int(val_mask.sum()),
        "n_test": int(test_mask.sum()),
        "n_pos_train": n_pos_train,
        "label_audit": label_audit,
        "normal": normal,
        "stress": stress,
        "extreme": extreme,
        "thresholds": thresholds,
        "trades": trades,
    }


def _build_labels_by_asset(df_base: pd.DataFrame, fold_year: int, horizon: int) -> pd.DataFrame:
    """Build forward labels per asset to avoid cross-symbol future returns."""
    take_profit, stop_loss = short_v2_exit_params(horizon)
    parts = []
    for _, group in df_base.groupby("asset_id", sort=True):
        group = group.sort_values("datetime").reset_index(drop=True)
        years = group["datetime"].dt.year.values
        group_train_mask = years <= fold_year - 2
        if group_train_mask.sum() < 100:
            group = group.copy()
            group[LABEL_COL] = -1
            group["short_v2_gray"] = 1
            group[CONTEXT_COL] = "none"
            group[ACTIVE_COL] = 0
            group[NO_SHORT_COL] = 1
            group[RET_COL] = np.nan
            group[MFE_COL] = np.nan
            group[MAE_COL] = np.nan
            parts.append(group)
            continue
        parts.append(
            build_short_v2_event_labels(
                group,
                group_train_mask,
                horizon=horizon,
                take_profit=take_profit,
                stop_loss=stop_loss,
            )
        )
    return pd.concat(parts, axis=0, ignore_index=True).sort_values(["datetime", "asset_id"]).reset_index(drop=True)


def monthly_table(trades: list[dict], position_pct: float = 0.10) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=["Mois", "Trades", "Clusters", "Horizons", "WR", "PnL%", "PF", "$10k", "$25k", "$50k", "$100k", "$200k", "$500k"])
    df = pd.DataFrame(trades)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["month"] = df["datetime"].dt.to_period("M").astype(str)
    rows = []
    for month, g in df.groupby("month"):
        # One hedge allocation is split across simultaneous asset signals.
        net = g.groupby("datetime")["net_ret"].mean().astype(float).values
        trade_net = g["net_ret"].astype(float).values
        wins = net[net > 0]
        losses = net[net <= 0]
        pnl_pct = float(net.sum() * position_pct * 100.0)
        pf = float(wins.sum() / max(abs(losses.sum()), 1e-12))
        row = {
            "Mois": month,
            "Trades": int(len(g)),
            "Clusters": int(len(net)),
            "Horizons": ",".join(str(int(h)) for h in sorted(g["horizon"].dropna().unique())) if "horizon" in g.columns else "",
            "WR": f"{(trade_net > 0).mean()*100:.1f}%",
            "PnL%": f"{pnl_pct:+.2f}%",
            "PF": round(pf, 3),
        }
        for capital in (10_000, 25_000, 50_000, 100_000, 200_000, 500_000):
            row[f"${capital//1000}k"] = round(capital * pnl_pct / 100.0, 2)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("Mois")


def select_combined_horizon_trades(trades: list[dict]) -> list[dict]:
    """Keep one horizon per timestamp/symbol/context using score margin only."""
    if not trades:
        return []
    ranked = []
    for trade in trades:
        margin = float(trade.get("p_short", 0.0)) - float(trade.get("threshold", 0.0))
        ranked.append((margin, -int(trade.get("horizon") or 10_000), trade))
    best: dict[tuple[str, str, str], tuple[float, int, dict]] = {}
    for item in ranked:
        _, _, trade = item
        key = (
            str(trade.get("datetime")),
            str(trade.get("symbol")),
            str(trade.get("context")),
        )
        if key not in best or item[:2] > best[key][:2]:
            best[key] = item
    return [item[2] for item in sorted(best.values(), key=lambda x: str(x[2].get("datetime")))]


def yearly_trade_summary(trades: list[dict], position_pct: float = 0.10) -> list[dict]:
    if not trades:
        return []
    df = pd.DataFrame(trades)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["year"] = df["datetime"].dt.year
    rows = []
    for year, g in df.groupby("year"):
        cluster_net = g.groupby("datetime")["net_ret"].mean().astype(float).values
        trade_net = g["net_ret"].astype(float).values
        wins = cluster_net[cluster_net > 0.0]
        losses = cluster_net[cluster_net <= 0.0]
        pf = float(wins.sum() / max(abs(losses.sum()), 1e-12)) if len(cluster_net) else 0.0
        rows.append(
            {
                "year": int(year),
                "trades": int(len(g)),
                "clusters": int(len(cluster_net)),
                "wr": round(float((trade_net > 0.0).mean()), 4) if len(trade_net) else 0.0,
                "pf": round(pf, 4),
                "expectancy": round(float(cluster_net.mean()), 6) if len(cluster_net) else 0.0,
                "pnl_pct": round(float(cluster_net.sum() * position_pct * 100.0), 4),
                "horizons": sorted(int(h) for h in g["horizon"].dropna().unique()) if "horizon" in g.columns else [],
            }
        )
    return rows


def _to_markdown_fallback(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    try:
        return df.to_markdown(index=False)
    except ImportError:
        headers = list(df.columns)
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        for _, row in df.iterrows():
            lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")
        return "\n".join(lines)


def compute_verdict(folds: list[dict], deployment_grade_data: bool) -> str:
    run = [r for r in folds if r.get("status") == "RUN"]
    if not run or any(r.get("fold_catastrophic") for r in run):
        return "SHORT_V2_REJECTED"
    ok = sum(1 for r in run if r.get("fold_ok"))
    total_trades = sum(r["stress"]["n_trades"] for r in run)
    if total_trades == 0:
        return "SHORT_V2_NO_TRADE_EDGE"
    if ok == len(run) and total_trades >= 100 and deployment_grade_data:
        return "SHORT_V2_HEDGE_PAPER_CANDIDATE"
    if ok == len(run) and total_trades >= 100:
        return "SHORT_V2_RESEARCH_ONLY_DATA_INCOMPLETE"
    return "SHORT_V2_PROMISING_BUT_UNSAFE"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(DATAOUT_DIR))
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--max-assets", type=int, default=10)
    parser.add_argument("--folds", nargs="+", type=int, default=[2021, 2022, 2023, 2024, 2025, 2026])
    parser.add_argument("--horizons", nargs="+", type=int, default=list(SHORT_V2_DEFAULT_HORIZONS))
    parser.add_argument("--max-train-rows", type=int, default=180_000)
    parser.add_argument("--allow-liquidation-proxy", action="store_true", default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("\nSHORT v2 DATA_OUT walk-forward")
    print(f"  data_dir={args.data_dir}")
    print(f"  folds={args.folds}")
    print(f"  horizons={args.horizons}")
    df, contract_reports = load_dataout_assets(Path(args.data_dir), args.symbols, args.max_assets, args.allow_liquidation_proxy)
    features = _features_available(df)
    print(f"  combined rows={len(df):,} assets={df['symbol'].nunique()} features={len(features)}")
    if len(features) < 20:
        raise RuntimeError(f"too few features: {len(features)}")

    horizon_results = []
    all_horizon_trades = []
    all_fold_results = []
    for horizon in sorted(set(int(h) for h in args.horizons)):
        fold_results = []
        print(f"\n### horizon {horizon}h ###")
        for fold in args.folds:
            try:
                fold_results.append(run_fold(df, fold, features, args.max_train_rows, horizon))
            except Exception as exc:
                print(f"  fold {fold} h={horizon} ERROR: {exc}")
                fold_results.append({"fold_year": fold, "horizon": horizon, "status": "ERROR", "error": str(exc)})

        horizon_trades = [trade for fold in fold_results for trade in fold.get("trades", [])]
        horizon_monthly = monthly_table(horizon_trades)
        horizon_csv = REPORT_DIR / f"monthly_results_dataout_v2_h{horizon}.csv"
        horizon_md = REPORT_DIR / f"monthly_results_dataout_v2_h{horizon}.md"
        horizon_trades_path = REPORT_DIR / f"trades_dataout_v2_h{horizon}.json"
        horizon_monthly.to_csv(horizon_csv, index=False)
        horizon_md.write_text(_to_markdown_fallback(horizon_monthly), encoding="utf-8")
        horizon_trades_path.write_text(json.dumps(horizon_trades, indent=2, default=str), encoding="utf-8")
        horizon_verdict = compute_verdict(fold_results, deployment_grade_data=False)
        horizon_results.append(
            {
                "horizon": horizon,
                "exit": {
                    "take_profit": round(short_v2_exit_params(horizon)[0], 6),
                    "stop_loss": round(short_v2_exit_params(horizon)[1], 6),
                },
                "verdict": horizon_verdict,
                "fold_results": fold_results,
                "yearly_results": yearly_trade_summary(horizon_trades),
                "monthly_results": horizon_monthly.to_dict("records"),
                "trades_path": str(horizon_trades_path),
            }
        )
        all_horizon_trades.extend(horizon_trades)
        all_fold_results.extend(fold_results)

    all_trades = select_combined_horizon_trades(all_horizon_trades)
    monthly = monthly_table(all_trades)
    monthly_csv = REPORT_DIR / "monthly_results_dataout_v2.csv"
    monthly_md = REPORT_DIR / "monthly_results_dataout_v2.md"
    monthly.to_csv(monthly_csv, index=False)
    monthly_md.write_text(_to_markdown_fallback(monthly), encoding="utf-8")

    deployment_grade_data = all(
        bool(r["contract"]["present_columns"].get("real_liquidations"))
        and not r["contract"].get("using_liquidation_proxy", False)
        for r in contract_reports
    )
    verdict = compute_verdict(all_fold_results, deployment_grade_data)
    if all_horizon_trades and not all_trades:
        verdict = "SHORT_V2_MULTI_HORIZON_NO_SELECTED_TRADES"
    summary = {
        "verdict": verdict,
        "deployment_mode": "hedge_only",
        "deployment_grade_data": deployment_grade_data,
        "live_allowed": False,
        "paper_allowed": verdict == "SHORT_V2_HEDGE_PAPER_CANDIDATE",
        "data_dir": str(args.data_dir),
        "n_assets": int(df["symbol"].nunique()),
        "horizons": sorted(set(int(h) for h in args.horizons)),
        "features": features,
        "data_contract": contract_reports,
        "horizon_results": horizon_results,
        "fold_results": all_fold_results,
        "all_horizon_trades_count": len(all_horizon_trades),
        "combined_selected_trades_count": len(all_trades),
        "combined_yearly_results": yearly_trade_summary(all_trades),
        "monthly_results": monthly.to_dict("records"),
        "duration_s": round(time.time() - t0, 1),
    }
    out = REPORT_DIR / "walk_forward_short_v2_dataout.json"
    trades_path = REPORT_DIR / "trades_dataout_v2.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    trades_path.write_text(json.dumps(all_trades, indent=2, default=str), encoding="utf-8")

    print("\nMONTHLY RESULTS")
    print(monthly.to_string(index=False))
    print(f"\nVERDICT: {verdict}")
    print(f"report: {out}")
    print(f"monthly: {monthly_csv}")
    print(f"trades: {trades_path}")


if __name__ == "__main__":
    main()
