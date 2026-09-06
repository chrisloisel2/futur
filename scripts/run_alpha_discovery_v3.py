#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.alpha_discovery_v3.pipeline import (
    FEATURE_GROUPS,
    causal_candidate_mask,
    enrich_symbol_frame,
    fit_predict_histgb,
    make_year_fold,
    summarize_folds,
)

PANEL_DIR = ROOT / "data_v2/normalized/event_feature_panel/venue=binance"
AGG_DIR = ROOT / "data_v2/normalized/agg_trades_flow/5m/venue=binance"
OI_DIR = ROOT / "data/derivatives_backfill/binance_vision_metrics"
IM_PATH = ROOT / "data_v2/instruments/instrument_master.parquet"
OUT_DIR = ROOT / "reports/alpha_discovery_v3"

RAW_AGG_COLS = [
    "timestamp", "trade_count", "large_trade_buy_usd", "large_trade_sell_usd",
    "avg_trade_size_usd", "p95_trade_size_usd", "buy_vwap", "sell_vwap",
]
RAW_OI_COLS = [
    "create_time", "sum_open_interest_value", "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
]


def load_partitioned(root: Path, symbol: str, filename: str, columns: list[str] | None = None) -> pd.DataFrame | None:
    paths = sorted((root / f"symbol={symbol}").glob(f"year=*/" + filename))
    if not paths:
        return None
    parts = []
    for path in paths:
        try:
            parts.append(pd.read_parquet(path, columns=columns))
        except Exception:
            df = pd.read_parquet(path)
            if columns:
                for c in columns:
                    if c not in df:
                        df[c] = np.nan
                df = df[columns]
            parts.append(df)
    return pd.concat(parts, ignore_index=True) if parts else None


def load_symbol_frame(symbol: str) -> pd.DataFrame | None:
    panel = load_partitioned(PANEL_DIR, symbol, "event_feature_panel_5m.parquet")
    if panel is None or panel.empty:
        return None
    panel["timestamp"] = pd.to_datetime(panel["timestamp"], utc=True)

    agg = load_partitioned(AGG_DIR, symbol, "flow.parquet", RAW_AGG_COLS)
    if agg is not None and not agg.empty:
        agg["timestamp"] = pd.to_datetime(agg["timestamp"], utc=True)
        agg = agg.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        panel = panel.merge(agg, on="timestamp", how="left", validate="one_to_one")
    else:
        for c in RAW_AGG_COLS[1:]:
            panel[c] = np.nan

    oi_path = OI_DIR / f"{symbol}_metrics_5m.parquet"
    if oi_path.exists():
        oi = pd.read_parquet(oi_path)
        for c in RAW_OI_COLS:
            if c not in oi:
                oi[c] = np.nan
        oi = oi[RAW_OI_COLS].rename(columns={"create_time": "timestamp"})
        oi["timestamp"] = pd.to_datetime(oi["timestamp"], utc=True)
        oi = oi.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        panel = panel.merge(oi, on="timestamp", how="left", validate="one_to_one")
    else:
        for c in RAW_OI_COLS[1:]:
            panel[c] = np.nan
    return panel.sort_values("timestamp").reset_index(drop=True)


def add_exact_costs(df: pd.DataFrame, tick_size: float | None, fee: float = 0.0005) -> pd.DataFrame:
    out = df.copy()
    if tick_size is None or not np.isfinite(tick_size) or tick_size < 0:
        out["cost_x1"] = np.nan
        out["cost_x2"] = np.nan
        return out
    px = out["entry_price"]
    c1 = 2 * fee + 2 * (float(tick_size) / px)
    c1 = c1.where(px > 0)
    out["cost_x1"] = c1
    out["cost_x2"] = 2 * c1
    return out


def build_sample(symbols: list[str], tick_map: dict[str, float], *, background_hours: int,
                 stress_threshold: float) -> pd.DataFrame:
    keep = []
    all_features = sorted({c for cols in FEATURE_GROUPS.values() for c in cols})
    for i, symbol in enumerate(symbols, 1):
        frame = load_symbol_frame(symbol)
        if frame is None or frame.empty:
            continue
        enriched = enrich_symbol_frame(frame)
        enriched = add_exact_costs(enriched, tick_map.get(symbol))
        mask = causal_candidate_mask(enriched, stress_threshold=stress_threshold, background_hours=background_hours)
        cols = [
            "timestamp", "target_residual_ret_1h", "target_path_complete_1h",
            "entry_price", "cost_x1", "cost_x2",
        ] + all_features
        cols = [c for c in cols if c in enriched.columns]
        sample = enriched.loc[mask, cols].copy()
        sample["symbol"] = symbol
        keep.append(sample)
        print(f"[{i:3}/{len(symbols)}] {symbol:14} rows={len(enriched):8,d} candidates={len(sample):7,d}", flush=True)
        del frame, enriched, sample
    return pd.concat(keep, ignore_index=True) if keep else pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser(description="Alpha Discovery V3: fixed A/B/C feature ablation on causal Data V2")
    ap.add_argument("--symbols", default=None, help="comma-separated subset; default all panel symbols")
    ap.add_argument("--background-hours", type=int, default=4)
    ap.add_argument("--stress-threshold", type=float, default=2.0)
    ap.add_argument("--test-years", default="2023,2024,2025,2026")
    ap.add_argument("--max-train-rows", type=int, default=500000)
    ap.add_argument("--max-calib-rows", type=int, default=150000)
    ap.add_argument("--max-test-rows", type=int, default=250000)
    ap.add_argument("--selection-quantile", type=float, default=0.95)
    ap.add_argument("--out", default=str(OUT_DIR / "RESULTS.json"))
    args = ap.parse_args()

    all_symbols = sorted(p.name.split("=", 1)[1] for p in PANEL_DIR.glob("symbol=*") if p.is_dir())
    symbols = all_symbols if not args.symbols else [s.strip() for s in args.symbols.split(",") if s.strip()]
    im = pd.read_parquet(IM_PATH)
    tick_map = dict(zip(im["symbol"], pd.to_numeric(im["tick_size"], errors="coerce")))

    dataset = build_sample(symbols, tick_map, background_hours=args.background_hours,
                           stress_threshold=args.stress_threshold)
    if dataset.empty:
        raise SystemExit("No candidate rows built")
    dataset = dataset.sort_values("timestamp").reset_index(drop=True)
    test_years = [int(x) for x in args.test_years.split(",")]

    result = {
        "protocol": {
            "primary_target": "next-bar-start residual return over 1h",
            "background_hours": args.background_hours,
            "stress_threshold": args.stress_threshold,
            "selection_quantile_abs_prediction": args.selection_quantile,
            "model": "sklearn HistGradientBoostingRegressor fixed params; no hyperparameter search",
            "feature_groups": FEATURE_GROUPS,
            "test_years": test_years,
        },
        "dataset": {
            "rows": len(dataset), "symbols": int(dataset["symbol"].nunique()),
            "start": str(dataset["timestamp"].min()), "end": str(dataset["timestamp"].max()),
        },
        "groups": {},
    }

    for group, features in FEATURE_GROUPS.items():
        folds = []
        for year in test_years:
            fold = make_year_fold(dataset["timestamp"], year)
            metrics = fit_predict_histgb(
                dataset, features, fold,
                max_train_rows=args.max_train_rows,
                max_calib_rows=args.max_calib_rows,
                max_test_rows=args.max_test_rows,
                selection_quantile=args.selection_quantile,
            )
            folds.append(metrics)
            print(group, year, metrics.get("status"), "IC=", metrics.get("ic_spearman"),
                  "N=", metrics.get("n"), "netx2=", metrics.get("net_x2_mean"), flush=True)
        result["groups"][group] = {"folds": folds, "summary": summarize_folds(folds)}

    a = result["groups"]["A_V1"]["summary"]
    b = result["groups"]["B_RAW"]["summary"]
    c = result["groups"]["C_DERIVED"]["summary"]
    result["ablation"] = {
        "B_minus_A_median_IC": b.get("median_ic_spearman", np.nan) - a.get("median_ic_spearman", np.nan),
        "C_minus_B_median_IC": c.get("median_ic_spearman", np.nan) - b.get("median_ic_spearman", np.nan),
        "C_minus_A_pooled_net_x2": c.get("pooled_net_x2_mean", np.nan) - a.get("pooled_net_x2_mean", np.nan),
        "interpretation_rule": (
            "If B/C fail to improve repeatedly OOS, under-use of existing features is not the main bottleneck. "
            "No feature tuning is authorized from this result."
        ),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=lambda x: None if isinstance(x, float) and not np.isfinite(x) else x))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
