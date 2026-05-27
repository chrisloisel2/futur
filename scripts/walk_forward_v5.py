#!/usr/bin/env python3
"""
scripts/walk_forward_v5.py — WALK-FORWARD v5 (TRM Fleet Long v5)
=================================================================

Objectif : valider ≥ 5%/mois de ROI sur chaque fold out-of-sample.
Pas de triche, pas de leakage — chiffres bruts.

Architecture v5 :
  • TRMFleetLongV5 (LightGBM DART, 24 TRM, stacking méta-LR)
  • Features : FEATURES_INST_LONG + FEATURES_ALPHA (48 nouvelles)
  • Labels adaptatifs au régime (quantile 0.75/0.78/0.82)
  • Gate NO_LONG maintenue
  • Threshold calibré sur val : Youden / F1-équilibré

Splits stricts (anti-leakage) :
  train  ≤ year-2
  val      year-1
  test     year    (BTC uniquement)

Usage :
  python scripts/walk_forward_v5.py
  python scripts/walk_forward_v5.py --folds 2023,2024,2025
  python scripts/walk_forward_v5.py --symbol ETHUSDT
  python scripts/walk_forward_v5.py --threshold 0.55
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_2.trm_fleet_long_v5 import TRMFleetLongV5
from ai.level_0.alpha_features     import compute_alpha_features, FEATURES_ALPHA
from ai.level_0.feature_engineering import compute_long_features, compute_flow_features
from ai.level_0.labels             import (
    compute_label_columns, compute_long_regime_col, build_labels,
)
from ai.level_0.constants          import TARGET_COL, COST_PCT
from ai.level_0.institutional_features import FEATURES_INST_LONG

try:
    from ai.level_0.features import get_available_features
except ImportError:
    def get_available_features(df, feats, min_fill=0.75, context=""):
        return [f for f in feats if f in df.columns and
                df[f].notna().mean() >= min_fill]

REPORT_DIR  = ROOT / "reports" / "walk_forward_v5"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR    = ROOT / "data" / "enriched"

DEPLOY_PF         = 1.25
TARGET_ROI_MONTH  = 5.0           # 5% / mois — critère principal (en %)
DEFAULT_THRESHOLD = 0.54          # seuil de base (calibré sur val)
MIN_TRADES_FOLD   = 8
HORIZON_BARS      = 8             # durée de chaque position (8h, alignée sur TARGET_COL)

# Taille de position pour la simulation séquentielle.
# Justification : Kelly fraction ≈ 40-80% pour PF=6-27, WR=78-87%.
# On prend 25% (fraction conservative = ~1/3 Kelly) pour simuler
# un paper trading prudent avec 1 position à la fois.
POSITION_SIZE_SEQ = 0.25


# ─────────────────────────────────────────────────────────────────────────────
# Chargement
# ─────────────────────────────────────────────────────────────────────────────

def _load(symbol: str) -> Optional[pd.DataFrame]:
    path = DATA_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        print(f"   ✗  {path.name} absent")
        return None
    try:
        df = pd.read_parquet(path)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"   ✗  {path.name} : {e}")
        return None


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule les features engineering + alpha (causal uniquement)."""
    df = compute_flow_features(df)
    df = compute_long_features(df)
    df = compute_alpha_features(df)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Métriques de fold
# ─────────────────────────────────────────────────────────────────────────────

def _metrics(
    trade_rets:  List[float],
    trade_dates: Optional[List[pd.Timestamp]] = None,
    test_df:     Optional[pd.DataFrame] = None,
) -> Dict:
    """
    Calcule les métriques sur une liste de rendements de trades (log-returns).

    Simulation séquentielle : 1 position à la fois, POSITION_SIZE_SEQ de l'equity.
    C'est la simulation réaliste du paper trading (pas de cumul de 1000 trades
    simultanés — chaque trade s'exclut mutuellement du suivant dans le temps).
    """
    if not trade_rets:
        return {"n_trades": 0, "pf": 0.0, "wr": 0.0, "roi_month": 0.0,
                "expectancy": 0.0, "max_drawdown_pct": 0.0, "total_pnl_pct": 0.0}

    arr   = np.array(trade_rets, dtype=np.float64)
    wins  = arr[arr > 0]
    losses= arr[arr < 0]
    pf    = float(wins.sum()) / max(float(abs(losses.sum())), 1e-9)
    wr    = len(wins) / len(arr)

    # Equity curve séquentielle : chaque trade déploie POSITION_SIZE_SEQ du capital
    equity = np.cumprod(1.0 + arr * POSITION_SIZE_SEQ)
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / np.maximum(peak, 1e-9)
    max_dd = float(abs(dd.min())) * 100.0

    total_ret = float(equity[-1] - 1.0)

    if test_df is not None and "datetime" in test_df.columns:
        n_months = max(
            (test_df["datetime"].iloc[-1] - test_df["datetime"].iloc[0]).days / 30.44,
            1.0,
        )
    else:
        n_months = 12.0
    roi_month = (((1.0 + total_ret) ** (1.0 / n_months)) - 1.0) * 100.0

    return {
        "n_trades":         len(arr),
        "pf":               round(pf, 3),
        "wr":               round(wr, 3),
        "roi_month":        round(roi_month, 2),
        "expectancy":       round(float(arr.mean()) * 100.0, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "total_pnl_pct":    round(total_ret * 100.0, 2),
    }


def _monthly_roi_breakdown(trade_rets: List[float],
                            trade_dates: List[pd.Timestamp]) -> List[Dict]:
    """Décompose le ROI par mois calendaire (détection surfit mensuel)."""
    if not trade_rets:
        return []
    df_m = pd.DataFrame({"ret": trade_rets, "date": trade_dates})
    df_m["ym"] = df_m["date"].dt.to_period("M")
    out = []
    for ym, grp in df_m.groupby("ym"):
        arr = grp["ret"].values * POSITION_SIZE_SEQ
        equity = np.cumprod(1.0 + arr)
        out.append({
            "month": str(ym),
            "trades": len(arr),
            "roi_pct": round((equity[-1] - 1.0) * 100.0, 2),
            "pf":      round(
                float(arr[arr > 0].sum()) / max(float(abs(arr[arr < 0].sum())), 1e-9), 2
            ),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Calibration du seuil sur val
# ─────────────────────────────────────────────────────────────────────────────

def _calibrate_threshold(
    df_val:       pd.DataFrame,
    fleet:        TRMFleetLongV5,
    feature_list: List[str],
    base_thr:     float = DEFAULT_THRESHOLD,
    min_trades_month: float = 12.0,  # plancher : au moins 12 trades/mois
) -> float:
    """
    Cherche le seuil qui maximise le F1 pondéré sur val, sous contrainte
    d'un minimum de signaux mensuels (pour éviter les folds à trop peu de trades).

    Anti-leakage : val est temporellement APRÈS train.
    """
    if "y_long" not in df_val.columns:
        return base_thr

    n = len(df_val)
    if n < 50:
        return base_thr

    ones = np.ones(n, dtype=bool)
    p    = fleet.predict(df_val, ones)
    y    = df_val["y_long"].values.astype(np.int32)
    no_long = (df_val.get("regime_long", pd.Series("NEUTRAL", index=df_val.index))
               .values == "NO_LONG")
    valid = (y >= 0) & ~no_long
    p_v, y_v = p[valid], y[valid]

    if y_v.sum() < 5:
        return base_thr

    # Estimation du nb de mois dans la période de val
    if "datetime" in df_val.columns:
        n_months_val = max(
            (df_val["datetime"].iloc[-1] - df_val["datetime"].iloc[0]).days / 30.44, 1.0
        )
    else:
        n_months_val = max(n / 720.0, 1.0)

    # Nb de trades séquentiels estimé (conservateur : 1 trade / HORIZON_BARS barres signal)
    # On divise par HORIZON_BARS pour approximer la fréquence non-chevauchante
    def estimated_seq_trades(thr: float) -> float:
        n_sig = int((p_v >= thr).sum())
        return (n_sig / HORIZON_BARS) / n_months_val  # trades/mois approx

    best_thr, best_score = base_thr, -1.0
    for thr in np.arange(0.40, 0.80, 0.01):
        pred = (p_v >= thr).astype(int)
        tp = int(((pred == 1) & (y_v == 1)).sum())
        fp = int(((pred == 1) & (y_v == 0)).sum())
        fn = int(((pred == 0) & (y_v == 1)).sum())
        prec = tp / max(tp + fp, 1)
        rec  = tp / max(tp + fn, 1)
        f1   = 2 * prec * rec / max(prec + rec, 1e-9)

        est_trades = estimated_seq_trades(thr)
        # Pénaliser si trop peu de trades (objectif volume minimum)
        trade_bonus = min(est_trades / min_trades_month, 1.0)
        score = f1 * trade_bonus

        if score > best_score and prec >= 0.60:  # garde-fou précision minimale
            best_score, best_thr = score, float(thr)

    return best_thr


# ─────────────────────────────────────────────────────────────────────────────
# Run d'un fold
# ─────────────────────────────────────────────────────────────────────────────

def run_fold(
    df:          pd.DataFrame,
    test_year:   int,
    features:    List[str],
    extra_dfs:   Optional[List[pd.DataFrame]] = None,
    threshold:   float = DEFAULT_THRESHOLD,
    use_no_long_gate: bool = True,
) -> Dict:
    years      = df["datetime"].dt.year.values
    train_mask = years <= (test_year - 2)
    val_mask   = years == (test_year - 1)
    test_mask  = years == test_year

    if train_mask.sum() < 2000 or val_mask.sum() < 500 or test_mask.sum() < 200:
        return {"year": test_year, "skip": True, "reason": "insufficient_data"}

    print(f"\n  ── Fold {test_year}  "
          f"[train≤{test_year-2}: {int(train_mask.sum()):,}]  "
          f"[val {test_year-1}: {int(val_mask.sum()):,}]  "
          f"[test {test_year}: {int(test_mask.sum()):,}]")

    # Labels (anti-leakage : sur df entier avant split)
    if TARGET_COL not in df.columns:
        df = compute_label_columns(df)
    if "regime_long" not in df.columns:
        df = compute_long_regime_col(df)
    try:
        df, _ = build_labels(df, train_mask)
    except Exception as e:
        print(f"   ⚠  build_labels: {e}")

    # Pool multi-actif pour le training
    dfs_train = [df.loc[train_mask].copy()]
    if extra_dfs:
        for dex in extra_dfs:
            yrs_ex = dex["datetime"].dt.year.values
            tm_ex  = yrs_ex <= (test_year - 2)
            if tm_ex.sum() < 500:
                continue
            coverage = sum(1 for f in features if f in dex.columns) / max(len(features), 1)
            if coverage < 0.60:
                continue
            if TARGET_COL not in dex.columns:
                dex = compute_label_columns(dex)
            if "regime_long" not in dex.columns:
                dex = compute_long_regime_col(dex)
            try:
                dex, _ = build_labels(dex, tm_ex)
            except Exception:
                pass
            dfs_train.append(dex.loc[tm_ex].copy())

    df_train_pool = pd.concat(dfs_train, ignore_index=True) if len(dfs_train) > 1 else dfs_train[0]
    n_extras = len(dfs_train) - 1
    if n_extras > 0:
        print(f"   Multi-actif : +{n_extras} actifs dans train")

    # Indices globaux pour train/val dans df original
    n_full        = len(df)
    full_train    = np.zeros(n_full, dtype=bool)
    full_train[train_mask] = True
    val_df        = df.loc[val_mask].copy()
    test_df       = df.loc[test_mask].copy()

    # Feature validation (fill ≥ 70%)
    feats_avail = get_available_features(df_train_pool, features, min_fill=0.70, context="v5")
    print(f"   Features actives : {len(feats_avail)} / {len(features)}")
    if len(feats_avail) < 10:
        return {"year": test_year, "skip": True, "reason": "too_few_features"}

    # Entraîner v5 — val_mask sur val de BTC (non présent dans pool si multi-actif)
    fleet = TRMFleetLongV5(features_base=feats_avail, features_alpha=[])
    pool_train_mask = np.ones(len(df_train_pool), dtype=bool)

    # Construire un val_mask dans le pool (BTC val = dernières lignes du premier df)
    # Uniquement si BTC seul (pool = 1 actif)
    if len(dfs_train) == 1 and "datetime" in df_train_pool.columns:
        pool_val_mask = df_train_pool["datetime"].dt.year.values == (test_year - 1)
        pool_train_for_fit = df_train_pool["datetime"].dt.year.values <= (test_year - 2)
    else:
        pool_val_mask   = None
        pool_train_for_fit = pool_train_mask

    fleet.fit(
        df_train_pool, pool_train_for_fit,
        val_mask=pool_val_mask if (pool_val_mask is not None and pool_val_mask.sum() > 100) else None,
        target_col=TARGET_COL,
        regime_col="regime_long",
    )

    # Calibrer le seuil sur val
    if "y_long" in val_df.columns:
        thr = _calibrate_threshold(val_df, fleet, feats_avail, base_thr=threshold)
        print(f"   Threshold calibré sur val : {thr:.2f}")
    else:
        thr = threshold

    # Backtest sur test — simulation séquentielle (1 position à la fois)
    n_test = len(test_df)
    ones   = np.ones(n_test, dtype=bool)
    p_long = fleet.predict(test_df, ones)

    # Gate NO_LONG
    if use_no_long_gate and "regime_long" in test_df.columns:
        no_long = test_df["regime_long"].values == "NO_LONG"
    else:
        no_long = np.zeros(n_test, dtype=bool)

    target_arr = test_df[TARGET_COL].fillna(0.0).values if TARGET_COL in test_df.columns \
                 else np.zeros(n_test)

    dates_arr = test_df["datetime"].values if "datetime" in test_df.columns else None

    trade_rets:  List[float]        = []
    trade_dates: List[pd.Timestamp] = []
    n_pos = 0          # compteur signaux totaux (avant cooldown)
    cooldown = 0       # barres restantes dans la position en cours

    for i in range(n_test):
        if cooldown > 0:
            cooldown -= 1
            continue
        if no_long[i]:
            continue
        if p_long[i] >= thr:
            n_pos += 1
            net = float(target_arr[i]) - COST_PCT
            trade_rets.append(net)
            trade_dates.append(
                pd.Timestamp(dates_arr[i]) if dates_arr is not None
                else pd.Timestamp("2000-01-01")
            )
            cooldown = HORIZON_BARS  # bloquer les 8 barres suivantes (position tenue)

    m = _metrics(trade_rets, trade_dates, test_df)
    monthly = _monthly_roi_breakdown(trade_rets, trade_dates)

    n_signals_total = int((p_long >= thr).sum())
    print(f"   signals_total={n_signals_total}  "
          f"n_trades_seq={m['n_trades']}  "
          f"PF={m['pf']:.3f}  WR={m['wr']:.1%}  "
          f"ROI/mois={m['roi_month']:+.2f}%  "
          f"MaxDD={m['max_drawdown_pct']:.1f}%  "
          f"[sizing={POSITION_SIZE_SEQ*100:.0f}%]")

    if monthly:
        neg_months = [x for x in monthly if x["roi_pct"] < 0]
        print(f"   Mois positifs : {len(monthly)-len(neg_months)}/{len(monthly)}  "
              f"| Mois négatifs : {neg_months}")

    target_ok = m["roi_month"] >= TARGET_ROI_MONTH
    pf_ok     = m["pf"] >= DEPLOY_PF
    trades_ok = m["n_trades"] >= MIN_TRADES_FOLD

    return {
        "year":        test_year,
        "skip":        False,
        "threshold":   thr,
        "fleet_auc":   fleet.fleet_auc_,
        "meta_auc":    fleet.meta_.val_auc_ if fleet.meta_ else 0.5,
        **m,
        "monthly":     monthly,
        "target_ok":   target_ok,
        "pf_ok":       pf_ok,
        "trades_ok":   trades_ok,
        "fold_ok":     target_ok and pf_ok and trades_ok,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward v5 — TRM Fleet Long v5")
    parser.add_argument("--symbol",    default="BTCUSDT", help="Symbole principal")
    parser.add_argument("--folds",     default="2022,2023,2024,2025",
                        help="Années de test (fold), comma-séparées")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Seuil p_long de base (défaut {DEFAULT_THRESHOLD})")
    parser.add_argument("--no-gate",   action="store_true",
                        help="Désactiver la gate NO_LONG")
    parser.add_argument("--no-extra",  action="store_true",
                        help="Training BTC uniquement (pas de pool multi-actif)")
    args = parser.parse_args()

    test_years = [int(y) for y in args.folds.split(",")]

    print(f"\n{'='*70}")
    print(f"  WALK-FORWARD v5 — {args.symbol}")
    print(f"  Folds : {test_years}")
    print(f"  Objectif : ROI ≥ {TARGET_ROI_MONTH:.0f}%/mois | PF ≥ {DEPLOY_PF}")
    print(f"{'='*70}\n")

    # Chargement + feature engineering
    print("▶ Chargement données...")
    df_primary = _load(args.symbol)
    if df_primary is None:
        sys.exit(1)

    print("▶ Feature engineering (alpha + flow + long)...")
    df_primary = _enrich(df_primary)
    print(f"   {args.symbol} : {len(df_primary):,} barres  [{len(df_primary.columns)} colonnes]")

    # Actifs supplémentaires
    extra_dfs: List[pd.DataFrame] = []
    if not args.no_extra:
        for path in sorted(DATA_DIR.glob("*_1h_enriched.parquet")):
            sym = path.stem.replace("_1h_enriched", "")
            if sym == args.symbol:
                continue
            dex = _load(sym)
            if dex is not None and len(dex) >= 5000:
                dex = _enrich(dex)
                extra_dfs.append(dex)
        print(f"   Extra actifs enrichis : {len(extra_dfs)}")

    # Feature list v5 = INST_LONG + ALPHA + feature_engineering extras
    feat_extra_eng = [
        "mom_logret_4", "mom_logret_8", "mom_logret_168", "vol_ratio_4h",
        "dist_from_local_low_24", "dist_from_local_low_168", "breakout_strength_24",
        "trend_persistence_12", "ret_pos_autocorr_12", "upside_vol_ratio_24",
        "taker_buy_cumul_12", "buy_vol_ratio_6", "momentum_accel_6", "boll_expansion_6",
        "volume_delta", "vol_imbalance", "trade_intensity",
        "liq_long_spike_12", "liq_short_spike_12", "liq_imbalance",
    ]
    features_v5 = list(dict.fromkeys(FEATURES_INST_LONG + feat_extra_eng + FEATURES_ALPHA))
    print(f"   Feature candidates : {len(features_v5)}")

    # Walk-forward
    results: List[Dict] = []
    for year in test_years:
        res = run_fold(
            df_primary,
            test_year=year,
            features=features_v5,
            extra_dfs=extra_dfs if not args.no_extra else None,
            threshold=args.threshold,
            use_no_long_gate=not args.no_gate,
        )
        results.append(res)

    # ── Résumé final ──────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  RÉSUMÉ WALK-FORWARD v5")
    print(f"{'='*70}")

    valid_folds = [r for r in results if not r.get("skip")]
    ok_folds    = [r for r in valid_folds if r.get("fold_ok")]

    header = f"{'Fold':<6} {'Fleet AUC':>10} {'PF':>7} {'WR':>7} {'ROI/mois':>10} {'MaxDD':>7} {'Trades':>7} {'OK?':>5}"
    print(header)
    print("-" * len(header))
    for r in results:
        if r.get("skip"):
            print(f"  {r['year']}  SKIP ({r.get('reason','')})")
            continue
        ok_str = "✓" if r.get("fold_ok") else "✗"
        print(f"  {r['year']:<4}  "
              f"AUC={r.get('fleet_auc',0):.4f}  "
              f"PF={r.get('pf',0):.3f}  "
              f"WR={r.get('wr',0):.1%}  "
              f"ROI={r.get('roi_month',0):+.2f}%/m  "
              f"DD={r.get('max_drawdown_pct',0):.1f}%  "
              f"n={r.get('n_trades',0)}  "
              f"{ok_str}")

    if valid_folds:
        rois   = [r["roi_month"]    for r in valid_folds]
        pfs    = [r["pf"]           for r in valid_folds]
        aucs   = [r.get("fleet_auc", 0.5) for r in valid_folds]
        print(f"\n  Médiane ROI/mois : {np.median(rois):+.2f}%")
        print(f"  Médiane PF       : {np.median(pfs):.3f}")
        print(f"  Médiane AUC      : {np.median(aucs):.4f}")
        print(f"  Folds OK         : {len(ok_folds)} / {len(valid_folds)}")
        print(f"  Objectif (5%/m)  : {'✓ ATTEINT' if np.median(rois) >= 5.0 else '✗ PAS ENCORE'}")

    # Sauvegarde JSON
    out_path = REPORT_DIR / "results_v5.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Rapport sauvé : {out_path}")

    # Exit code selon l'objectif
    if valid_folds and np.median([r["roi_month"] for r in valid_folds]) >= 5.0:
        print("\n  ✓ OBJECTIF 5%/MOIS ATTEINT\n")
        sys.exit(0)
    else:
        print("\n  ✗ Objectif non atteint — continuer l'itération\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
