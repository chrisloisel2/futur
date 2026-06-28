#!/usr/bin/env python3
"""
scripts/simple_model_test.py — Test d'edge avec modèle simple (LightGBM)
=========================================================================

Avant de re-tuner le TRM complexe, valider l'existence d'un edge avec
un modèle intentionnellement simple.

Protocole walk-forward strict :
  Pour chaque année test ∈ [2022, 2023, 2024, 2025] :
    → Train sur toutes les données antérieures
    → Cal  sur H2 année précédente (calibration seuil)
    → Test sur l'année (PF, WR, n_trades)
  Modèle : LightGBM binaire (y_long = {0,1})

Features :
  - OHLCV + indicateurs techniques (base)
  - CVD + OI + basis (microstructure)
  Toutes normalisées par asset.

Critères de décision :
  PF OOS > 1.20 sur ≥50 trades → PROMOTE vers TRM complet
  PF OOS < 1.00 sur ≥30 trades → REJECT — labels ou data insuffisants

Usage :
  python scripts/simple_model_test.py
  python scripts/simple_model_test.py --symbols BTCUSDT ETHUSDT
  python scripts/simple_model_test.py --feature-set micro  # base|micro|all
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_0.labels import compute_label_columns, build_labels, compute_long_regime_col
from ai.level_0.constants import TARGET_COL, COST_PCT, HORIZON_BARS

try:
    import lightgbm as lgb
    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

ENRICHED_DIR = ROOT / "data" / "enriched"
TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]
SIZING   = 0.25
CAL_MONTH_START = 7  # juillet

# ── Feature sets ──────────────────────────────────────────────────────────────

FEATURES_BASE = [
    "return_5", "return_10", "return_20", "log_return_5", "log_return_10",
    "realized_vol_20", "atr_pct_20", "bb_width_20", "bb_percent_b_20",
    "close_position_in_range", "body_to_range",
    "distance_ema_20", "distance_ema_50", "distance_ema_200",
    "ema_slope_20", "ema_21_50_spread",
    "macd_hist", "macd_hist_slope",
    "rsi_13", "rsi_20", "stoch_k_20",
    "adx_20", "di_spread_20", "choppiness_20",
    "volume_ratio_20", "cmf_20",
    "trend_score", "momentum_score", "volatility_score",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "mtf_4h_adx_20", "mtf_4h_rsi_10", "mtf_4h_return_5",
    "mtf_1d_return_5", "mtf_1d_rsi_5",
]

FEATURES_MICRO = [
    "cvd_4h_z", "cvd_24h_z", "cvd_momentum",
    "oi_delta_8h", "oi_delta_24h", "oi_price_regime",
    "basis_annualized", "basis_momentum_8h", "basis_extreme_long",
    "taker_buy_ratio_base", "taker_flow_imbalance_20",
    "funding_rate", "funding_rate_z_24", "funding_rate_z_72",
]


def _add_micro_features(df: pd.DataFrame) -> pd.DataFrame:
    from scripts.live_data_update import (
        _add_cvd_features, _add_oi_features, _add_basis_features,
        _add_taker_flow_features,
    )
    df = _add_cvd_features(df)
    df = _add_oi_features(df)
    df = _add_basis_features(df)
    df = _add_taker_flow_features(df)
    return df


# ── Modèle simple ──────────────────────────────────────────────────────────────

def _train_simple(X_tr, y_tr, use_lgb=True):
    """LightGBM si disponible, sinon HistGradientBoosting."""
    n_pos = int(y_tr.sum())
    n_neg = len(y_tr) - n_pos
    scale = min(n_neg / max(n_pos, 1), 30.0)

    if use_lgb and _HAS_LGB:
        params = {
            "objective":       "binary",
            "n_estimators":    300,
            "learning_rate":   0.05,
            "num_leaves":      31,
            "max_depth":       5,
            "min_child_samples": 20,
            "scale_pos_weight": scale,
            "verbose":         -1,
            "n_jobs":          4,
        }
        model = lgb.LGBMClassifier(**params)
    else:
        model = HistGradientBoostingClassifier(
            max_iter=300, max_depth=5, learning_rate=0.05,
            min_samples_leaf=20,
            class_weight={0: 1.0, 1: scale},
            random_state=42,
        )

    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X_tr)
    model.fit(X_sc, y_tr)
    return model, scaler


def _simulate(
    df_test: pd.DataFrame,
    model, scaler,
    features: List[str],
    thr: float = 0.55,
) -> Tuple[List[float], int]:
    """Retourne (pnls, n_trades)."""
    avail = [f for f in features if f in df_test.columns]
    if not avail:
        return [], 0

    X = df_test[avail].fillna(0.0).values
    X_sc = scaler.transform(X)
    p = model.predict_proba(X_sc)[:, 1]

    close = df_test["close"].values
    regime = (df_test["regime_long"].values
              if "regime_long" in df_test.columns
              else np.full(len(df_test), "NEUTRAL"))
    pnls = []
    for si in range(len(df_test)):
        if p[si] < thr:
            continue
        if str(regime[si]) == "NO_LONG":
            continue
        if si + HORIZON_BARS >= len(df_test):
            continue
        ret = float(np.log(close[si + HORIZON_BARS] / max(close[si], 1e-9)) - COST_PCT)
        pnls.append(ret * SIZING * 100)

    return pnls, len(pnls)


def _pf(pnls: List[float]) -> Tuple[float, float, int]:
    if not pnls:
        return 0.0, 0.0, 0
    wins  = [p for p in pnls if p > 0]
    loss  = [abs(p) for p in pnls if p < 0]
    pf    = sum(wins) / max(sum(loss), 1e-9)
    wr    = len(wins) / len(pnls)
    return round(pf, 3), round(wr, 3), len(pnls)


# ── Walk-forward ───────────────────────────────────────────────────────────────

def run_simple_wf(
    symbols:      List[str],
    test_years:   List[int],
    feature_set:  str = "all",
    use_lgb:      bool = True,
) -> None:

    feats_to_use = (
        FEATURES_BASE + FEATURES_MICRO if feature_set == "all" else
        FEATURES_BASE if feature_set == "base" else
        FEATURES_MICRO
    )

    print("=" * 72)
    print(f"  SIMPLE MODEL TEST — {'LightGBM' if use_lgb and _HAS_LGB else 'HistGBM'}")
    print(f"  Feature set : {feature_set} ({len(feats_to_use)} features)")
    print(f"  Critère GO  : PF OOS > 1.20 sur ≥50 trades")
    print(f"  Critère STOP: PF OOS < 1.00 sur ≥30 trades")
    print("=" * 72)

    all_pnls_by_year: Dict[int, List[float]] = defaultdict(list)
    all_pnls_by_asset: Dict[str, List[float]] = defaultdict(list)

    for sym in symbols:
        path = ENRICHED_DIR / f"{sym}_1h_enriched.parquet"
        if not path.exists():
            continue

        sname = sym.replace("USDT", "")
        df = pd.read_parquet(path)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        # rv aliases
        if "rv_24" not in df.columns and "realized_volatility_20" in df.columns:
            df["rv_24"] = df["realized_volatility_20"]
        if "rv_72" not in df.columns and "realized_volatility_50" in df.columns:
            df["rv_72"] = df["realized_volatility_50"]

        df = _add_micro_features(df)
        df = df.sort_values("datetime").reset_index(drop=True)
        df = compute_label_columns(df)
        df = compute_long_regime_col(df)

        years  = df["datetime"].dt.year.values
        dt_col = pd.to_datetime(df["datetime"], utc=True)

        avail_feats = [f for f in feats_to_use if f in df.columns]
        if len(avail_feats) < 5:
            print(f"  [{sname}] trop peu de features dispo ({len(avail_feats)}) — skip")
            continue

        print(f"\n  [{sname}] {len(avail_feats)} features disponibles")
        print(f"  {'Année':<6} {'PF':>6} {'WR':>6} {'n':>5} {'PnL':>8} {'AUC':>6}")
        print("  " + "─" * 40)

        for test_year in test_years:
            cal_mask = (years == test_year - 1) & (dt_col.dt.month >= CAL_MONTH_START)
            tr_mask  = (years < test_year) & ~cal_mask
            tst_mask = years == test_year

            if tr_mask.sum() < 500 or tst_mask.sum() < 100:
                continue

            # Labels sur train
            df_work = df.copy()
            try:
                df_work, _ = build_labels(df_work, tr_mask)
            except Exception:
                continue

            y_all = df_work["y_long"].values
            y_tr  = y_all[tr_mask]
            valid_tr = y_tr >= 0
            X_tr = df_work.loc[tr_mask][avail_feats].fillna(0.0).values[valid_tr]
            y_tr = y_tr[valid_tr]

            n_pos = int((y_tr == 1).sum())
            if n_pos < 10:
                continue

            # Train
            try:
                t0 = time.time()
                model, scaler = _train_simple(X_tr, (y_tr == 1).astype(int), use_lgb)
            except Exception as e:
                print(f"    {test_year} ERROR train: {e}")
                continue

            # Calibration threshold sur H2 année précédente
            df_cal = df_work.loc[cal_mask].copy().reset_index(drop=True)
            thr = 0.55
            if len(df_cal) >= 20:
                avail_c = [f for f in avail_feats if f in df_cal.columns]
                X_c     = df_cal[avail_c].fillna(0.0).values
                X_c_sc  = scaler.transform(X_c)
                p_c     = model.predict_proba(X_c_sc)[:, 1]
                ret_c   = df_cal[TARGET_COL].fillna(0.0).values if TARGET_COL in df_cal.columns else np.zeros(len(df_cal))
                best_pf, best_thr = -1.0, 0.55
                for t in np.arange(0.45, 0.70, 0.02):
                    sel = p_c >= t
                    if sel.sum() < 5:
                        continue
                    w = float(ret_c[sel][ret_c[sel] > 0].sum())
                    l = float(abs(ret_c[sel][ret_c[sel] < 0].sum()))
                    pf_t = w / max(l, 1e-9)
                    if pf_t > best_pf:
                        best_pf, best_thr = pf_t, t
                thr = best_thr

            # AUC sur calibration
            auc_str = "   —"
            if len(df_cal) >= 20:
                try:
                    y_c = (df_cal["y_long"].fillna(0) == 1).astype(int).values
                    if len(np.unique(y_c)) > 1:
                        auc = roc_auc_score(y_c, p_c)
                        auc_str = f"{auc:.3f}"
                except Exception:
                    pass

            # Simulation sur test
            df_tst = df_work.loc[tst_mask].copy().reset_index(drop=True)
            pnls, n = _simulate(df_tst, model, scaler, avail_feats, thr)
            pf, wr, _ = _pf(pnls)
            total_pnl = sum(pnls)

            all_pnls_by_year[test_year].extend(pnls)
            all_pnls_by_asset[sname].extend(pnls)

            print(f"  {test_year:<6} {pf:>6.3f} {wr:>5.1%} {n:>5} {total_pnl:>+7.1f}%  {auc_str}  ({time.time()-t0:.0f}s)")

    # ── Rapport global ────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  RÉSULTATS GLOBAUX")
    print(f"  {'Année':<8} {'PF':>7} {'WR':>7} {'n':>6} {'PnL':>10} {'Moy/mois':>10}")
    print("  " + "─" * 56)

    all_pnls_total = []
    for yr in sorted(all_pnls_by_year):
        pnls = all_pnls_by_year[yr]
        pf, wr, n = _pf(pnls)
        tot = sum(pnls)
        mpm = tot / 12
        all_pnls_total.extend(pnls)
        print(f"  {yr:<8} {pf:>7.3f} {wr:>6.1%} {n:>6} {tot:>+9.1f}%  {mpm:>+8.2f}%/mois")

    print("  " + "─" * 56)
    pf_tot, wr_tot, n_tot = _pf(all_pnls_total)
    print(f"  {'TOTAL':<8} {pf_tot:>7.3f} {wr_tot:>6.1%} {n_tot:>6} {sum(all_pnls_total):>+9.1f}%")

    # ── Décision ──────────────────────────────────────────────────────────────
    print(f"\n  DÉCISION (PF global = {pf_tot:.3f}, n = {n_tot}) :")
    if pf_tot > 1.20 and n_tot >= 50:
        print(f"  ✓ PROMOTE — PF={pf_tot:.3f} > 1.20, n={n_tot} ≥ 50")
        print("  → Edge non-linéaire détecté. Retrain TRM avec ces features.")
        print("  → Lancer : python scripts/walkforward_v3.py --years 2022 2023 2024 2025 --save")
    elif pf_tot > 1.00 and n_tot >= 30:
        print(f"  ~ INCUBATE — PF={pf_tot:.3f} (entre 1.0 et 1.2)")
        print("  → Signal faible mais positif. Améliorer les features.")
    elif n_tot < 30:
        print(f"  ? INSUFFISANT — n={n_tot} < 30 trades — pas de décision possible")
    else:
        print(f"  ✗ REJECT — PF={pf_tot:.3f} < 1.00")
        print("  → Pas d'edge détecté avec ces features.")
        print("  → Causes probables : labels, horizon, données insuffisantes.")
        print("  → Prochaine étape : tester horizon 4h ou nouvelles données.")

    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols",     nargs="+",  default=TOP_10)
    parser.add_argument("--years",       nargs="+",  type=int,
                        default=[2022, 2023, 2024, 2025])
    parser.add_argument("--feature-set", default="all",
                        choices=["base", "micro", "all"])
    args = parser.parse_args()

    available = [s for s in args.symbols
                 if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    run_simple_wf(available, args.years, args.feature_set)


if __name__ == "__main__":
    main()
