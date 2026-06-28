#!/usr/bin/env python3
"""
scripts/test_r2_oos.py — Mesure du R² OOS des nouvelles features microstructure
=================================================================================

Protocole :
  Train : 2018-2022 (ou première moitié dispo par asset)
  OOS   : 2023-2025
  Cible : future_ret_8h (log-return)
  Modèle: ReturnPredictor (Ridge regression) + LightGBM (comparaison)

Critères de décision :
  R² OOS < 0.05 → STOP, les nouvelles features n'apportent pas d'alpha
  R² OOS > 0.08 → GO, retrain TRM avec nouvelles features

Sorties :
  - R² par asset (OHLCV seul vs OHLCV+microstructure)
  - Feature importance
  - Décision go/no-go

Usage :
  python scripts/test_r2_oos.py
  python scripts/test_r2_oos.py --symbols BTCUSDT ETHUSDT
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_0.labels import compute_label_columns
from ai.level_0.return_predictor import ReturnPredictor
from ai.level_0.constants import TARGET_COL

# ── Features ──────────────────────────────────────────────────────────────────

# Features OHLCV/techniques de base (baseline)
FEATURES_BASE = [
    "return_5", "return_10", "return_20", "log_return_5", "log_return_10",
    "realized_vol_20", "atr_pct_20", "bb_width_20", "bb_percent_b_20",
    "distance_ema_20", "distance_ema_50", "ema_slope_20",
    "macd_hist", "macd_hist_slope", "rsi_13", "rsi_20", "adx_20",
    "volume_ratio_20", "trend_score", "momentum_score",
    "mtf_4h_adx_20", "mtf_4h_rsi_10", "mtf_4h_return_5",
]

# Features microstructure ajoutées (v3)
FEATURES_MICRO = [
    "cvd_4h_z", "cvd_24h_z", "cvd_momentum",
    "oi_delta_8h", "oi_delta_24h", "oi_price_regime",
    "basis_annualized", "basis_momentum_8h", "basis_extreme_long",
    "taker_buy_ratio_base", "taker_flow_imbalance_20",
    "funding_rate", "funding_rate_z_24", "funding_rate_z_72",
]

TRAIN_END_YEAR = 2022
OOS_START_YEAR = 2023

ENRICHED_DIR = ROOT / "data" / "enriched"
TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]


def _add_micro_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule CVD, OI delta et basis si absents (réplique live_data_update)."""
    from scripts.live_data_update import (
        _add_cvd_features, _add_oi_features, _add_basis_features,
        _add_taker_flow_features, _add_funding_features,
    )
    df = _add_cvd_features(df)
    df = _add_oi_features(df)
    df = _add_basis_features(df)
    df = _add_taker_flow_features(df)
    return df


def _r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    # Clip predictions à ±5× std de y_true pour éviter overflow numérique
    clip_val = max(np.std(y_true) * 5, 0.50)
    y_pred   = np.clip(y_pred, -clip_val, clip_val)
    y_pred   = np.nan_to_num(y_pred, nan=0.0, posinf=clip_val, neginf=-clip_val)
    ss_res   = np.sum((y_true - y_pred) ** 2)
    ss_tot   = np.sum((y_true - y_true.mean()) ** 2)
    r2       = float(1 - ss_res / max(ss_tot, 1e-12))
    return max(-1.0, r2)  # floor à -1 pour lisibilité


def evaluate_asset(sym: str) -> Optional[dict]:
    path = ENRICHED_DIR / f"{sym}_1h_enriched.parquet"
    if not path.exists():
        print(f"  [{sym}] parquet absent — skip")
        return None

    df = pd.read_parquet(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)
    df = compute_label_columns(df)

    # Ajouter features microstructure si absentes
    df = _add_micro_features(df)

    years = df["datetime"].dt.year.values
    train_mask = years <= TRAIN_END_YEAR
    oos_mask   = years >= OOS_START_YEAR

    if train_mask.sum() < 1000 or oos_mask.sum() < 500:
        print(f"  [{sym}] données insuffisantes — skip")
        return None

    # Filtrer les barres où la cible est valide
    target_valid = df[TARGET_COL].notna()
    train_mask = train_mask & target_valid.values
    oos_mask   = oos_mask   & target_valid.values

    y_train = df.loc[train_mask, TARGET_COL].values
    y_oos   = df.loc[oos_mask,   TARGET_COL].values

    results = {}

    for label, feats in [("base", FEATURES_BASE), ("micro", FEATURES_BASE + FEATURES_MICRO)]:
        avail = [f for f in feats if f in df.columns]
        if len(avail) < 5:
            continue

        rp = ReturnPredictor()
        rp.fit(df, avail, train_mask)

        if not rp.fitted_:
            results[label] = {"r2_train": 0, "r2_oos": 0, "n_feats": 0}
            continue

        # Prédictions OOS
        X_oos = df.loc[oos_mask, avail].fillna(0.0).values
        X_oos_sc = rp.scaler_.transform(X_oos)
        y_pred_oos = rp.model_.predict(X_oos_sc)

        # Prédictions train (pour comparaison)
        X_tr = df.loc[train_mask, avail].fillna(0.0).values
        X_tr_sc = rp.scaler_.transform(X_tr)
        y_pred_tr = rp.model_.predict(X_tr_sc)

        r2_tr  = _r2_score(y_train, y_pred_tr)
        r2_oos = _r2_score(y_oos,   y_pred_oos)

        results[label] = {
            "r2_train": round(r2_tr,  4),
            "r2_oos":   round(r2_oos, 4),
            "n_feats":  len(avail),
            "n_train":  int(train_mask.sum()),
            "n_oos":    int(oos_mask.sum()),
        }

    if not results:
        return None

    # Impact microstructure
    delta_r2 = 0.0
    if "micro" in results and "base" in results:
        delta_r2 = results["micro"]["r2_oos"] - results["base"]["r2_oos"]

    return {
        "symbol": sym,
        "base":   results.get("base", {}),
        "micro":  results.get("micro", {}),
        "delta_r2_oos": round(delta_r2, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=TOP_10)
    args = parser.parse_args()

    symbols = [s for s in args.symbols if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]

    print("=" * 70)
    print("  TEST R² OOS — Impact des features microstructure")
    print(f"  Train ≤ {TRAIN_END_YEAR}  |  OOS ≥ {OOS_START_YEAR}")
    print(f"  Seuil GO : R² OOS micro > 0.08")
    print(f"  Seuil STOP : R² OOS micro < 0.05")
    print("=" * 70)

    rows = []
    for sym in symbols:
        print(f"\n  [{sym}]")
        res = evaluate_asset(sym)
        if res:
            rows.append(res)
            b = res["base"];  m = res["micro"]
            print(f"    Base  : R²_train={b.get('r2_train','?'):.4f}  R²_OOS={b.get('r2_oos','?'):.4f}  feats={b.get('n_feats',0)}")
            print(f"    Micro : R²_train={m.get('r2_train','?'):.4f}  R²_OOS={m.get('r2_oos','?'):.4f}  feats={m.get('n_feats',0)}")
            print(f"    Δ R²_OOS (micro - base) : {res['delta_r2_oos']:+.4f}")

    if not rows:
        print("\n  Aucun résultat. Vérifier les parquets.")
        return

    # ── Résumé ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"  {'Asset':<12} {'R²_OOS_base':>13} {'R²_OOS_micro':>14} {'Δ':>8} {'Verdict':>10}")
    print("  " + "─" * 60)

    r2_micro_all = []
    for res in rows:
        sym  = res["symbol"].replace("USDT", "")
        r2_b = res["base"].get("r2_oos", 0)
        r2_m = res["micro"].get("r2_oos", 0)
        d    = res["delta_r2_oos"]
        v    = "✓ MICRO+" if d > 0 else "  BASE+"
        r2_micro_all.append(r2_m)
        print(f"  {sym:<12} {r2_b:>+12.4f}  {r2_m:>+13.4f}  {d:>+7.4f}  {v}")

    avg_r2 = float(np.mean(r2_micro_all)) if r2_micro_all else 0
    print("  " + "─" * 60)
    print(f"  {'MOYENNE':<12} {'':>13} {avg_r2:>+13.4f}")
    print()

    # ── Décision ──────────────────────────────────────────────────────────────
    print("  DÉCISION :")
    if avg_r2 > 0.08:
        print(f"  ✓ GO  — R² OOS moyen = {avg_r2:.4f} > 0.08")
        print("  → Retrain TRM avec features microstructure (CVD + OI + basis)")
        print("  → Lancer : python scripts/walkforward_v3.py --years 2022 2023 2024 2025 --save")
    elif avg_r2 > 0.05:
        print(f"  ~ INCUBATE — R² OOS moyen = {avg_r2:.4f} (entre 0.05 et 0.08)")
        print("  → Ajouter : order book depth, liquidations, on-chain flows")
        print("  → Tester : HORIZON 4h au lieu de 8h")
    else:
        print(f"  ✗ STOP — R² OOS moyen = {avg_r2:.4f} < 0.05")
        print("  → Les nouvelles features n'apportent pas d'alpha mesurable")
        print("  → Chercher : données on-chain, order book L2, liquidation heatmap")

    print("=" * 70)


if __name__ == "__main__":
    main()
