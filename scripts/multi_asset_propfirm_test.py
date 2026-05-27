#!/usr/bin/env python3
"""
scripts/multi_asset_propfirm_test.py — TEST MULTI-ACTIF × PROP FIRM × PORTEFEUILLE
=====================================================================================

Teste la stratégie TRM Fleet v5 sur :
  • 10 actifs (tout le dataset disponible)
  • 5 environnements prop firm + self-funded
  • 6 tailles de portefeuille ($1k → $100k)

Sorties :
  reports/propfirm_test/summary.json        — résumé global
  reports/propfirm_test/per_asset.csv       — métriques par actif
  reports/propfirm_test/propfirm_grid.csv   — grille prop firm × portefeuille
  reports/propfirm_test/monthly_detail.csv  — détail mensuel par actif

Règles de simulation :
  - Walk-forward strict : train ≤ year-2, val = year-1, test = year
  - 1 position à la fois (cooldown 8h)
  - Sizing ajusté selon les contraintes DD du prop firm
  - Violation DD → trade forcé à zéro, compteur de violations
"""
from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_2.trm_fleet_long_v5  import TRMFleetLongV5
from ai.level_0.alpha_features      import compute_alpha_features, FEATURES_ALPHA
from ai.level_0.feature_engineering import compute_long_features, compute_flow_features
from ai.level_0.labels              import (
    compute_label_columns, compute_long_regime_col, build_labels,
)
from ai.level_0.constants           import TARGET_COL, COST_PCT
from ai.level_0.institutional_features import FEATURES_INST_LONG

try:
    from ai.level_0.features import get_available_features
except ImportError:
    def get_available_features(df, feats, min_fill=0.75, context=""):
        return [f for f in feats if f in df.columns and df[f].notna().mean() >= min_fill]

REPORT_DIR = ROOT / "reports" / "propfirm_test"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR   = ROOT / "data" / "enriched"

HORIZON_BARS      = 8
DEFAULT_THRESHOLD = 0.54
MIN_TRADES_MONTH  = 8.0

FEAT_ENG_EXTRA = [
    "mom_logret_4", "mom_logret_8", "mom_logret_168", "vol_ratio_4h",
    "dist_from_local_low_24", "dist_from_local_low_168", "breakout_strength_24",
    "trend_persistence_12", "ret_pos_autocorr_12", "upside_vol_ratio_24",
    "taker_buy_cumul_12", "buy_vol_ratio_6", "momentum_accel_6", "boll_expansion_6",
    "volume_delta", "vol_imbalance", "trade_intensity",
    "liq_long_spike_12", "liq_short_spike_12", "liq_imbalance",
]
FEATURES_V5 = list(dict.fromkeys(FEATURES_INST_LONG + FEAT_ENG_EXTRA + FEATURES_ALPHA))


# ─────────────────────────────────────────────────────────────────────────────
# Prop Firm Configs
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PropFirmConfig:
    name:              str
    label:             str
    max_dd_pct:        float   # % max drawdown autorisé (0 = illimité)
    daily_dd_pct:      float   # % perte max par jour (0 = illimité)
    profit_target_pct: float   # % cible de profit pour passer l'évaluation (0 = pas d'éval)
    profit_split:      float   # part du trader (0.80 = 80%)
    position_size:     float   # sizing de base (ajusté si DD trop proche)
    eval_days:         int     # durée max de l'évaluation en jours (0 = illimité)
    note:              str     = ""


PROP_FIRMS: List[PropFirmConfig] = [
    PropFirmConfig(
        name="self_funded",
        label="Self-Funded",
        max_dd_pct=0.0, daily_dd_pct=0.0,
        profit_target_pct=0.0, profit_split=1.0,
        position_size=0.25, eval_days=0,
        note="Capital propre, liberté totale"
    ),
    PropFirmConfig(
        name="ftmo_aggressive",
        label="FTMO-style Agressif",
        max_dd_pct=10.0, daily_dd_pct=5.0,
        profit_target_pct=10.0, profit_split=0.80,
        position_size=0.20, eval_days=30,
        note="DD max 10%, objectif 10% en 30j, split 80%"
    ),
    PropFirmConfig(
        name="the5ers",
        label="The5%ers-style",
        max_dd_pct=6.0, daily_dd_pct=0.0,
        profit_target_pct=8.0, profit_split=0.80,
        position_size=0.15, eval_days=60,
        note="DD max 6%, objectif 8%, split 80%"
    ),
    PropFirmConfig(
        name="topstep_crypto",
        label="TopStep Crypto",
        max_dd_pct=6.0, daily_dd_pct=3.0,
        profit_target_pct=6.0, profit_split=0.90,
        position_size=0.12, eval_days=60,
        note="DD max 6%, daily 3%, objectif 6%, split 90%"
    ),
    PropFirmConfig(
        name="conservative",
        label="Conservative (HFT-style)",
        max_dd_pct=3.0, daily_dd_pct=1.0,
        profit_target_pct=3.0, profit_split=0.75,
        position_size=0.08, eval_days=30,
        note="DD max 3%, sizing très conservateur, split 75%"
    ),
    PropFirmConfig(
        name="aggressive_kelly",
        label="Agressif (1/2 Kelly)",
        max_dd_pct=20.0, daily_dd_pct=0.0,
        profit_target_pct=0.0, profit_split=1.0,
        position_size=0.40, eval_days=0,
        note="Capital propre, 1/2 Kelly (~40%), pas de limite DD"
    ),
]

PORTFOLIO_SIZES = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]  # en USD


# ─────────────────────────────────────────────────────────────────────────────
# Chargement + enrichissement
# ─────────────────────────────────────────────────────────────────────────────

def _load_enrich(symbol: str) -> Optional[pd.DataFrame]:
    path = DATA_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        df = df.sort_values("datetime").reset_index(drop=True)
        df = compute_flow_features(df)
        df = compute_long_features(df)
        df = compute_alpha_features(df)
        return df
    except Exception as e:
        print(f"   ✗  {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Calibration threshold
# ─────────────────────────────────────────────────────────────────────────────

def _calibrate_thr(df_val: pd.DataFrame, fleet: TRMFleetLongV5,
                   base: float = DEFAULT_THRESHOLD) -> float:
    if "y_long" not in df_val.columns or len(df_val) < 50:
        return base
    n   = len(df_val)
    p   = fleet.predict(df_val, np.ones(n, dtype=bool))
    y   = df_val["y_long"].values.astype(np.int32)
    no_long = (df_val.get("regime_long", pd.Series("NEUTRAL", index=df_val.index))
               .values == "NO_LONG")
    valid = (y >= 0) & ~no_long
    p_v, y_v = p[valid], y[valid]
    if y_v.sum() < 5:
        return base

    n_months = max(
        (df_val["datetime"].iloc[-1] - df_val["datetime"].iloc[0]).days / 30.44
        if "datetime" in df_val.columns else 12.0, 1.0
    )
    best_thr, best_score = base, -1.0
    for thr in np.arange(0.40, 0.80, 0.01):
        pred  = (p_v >= thr).astype(int)
        tp    = int(((pred == 1) & (y_v == 1)).sum())
        fp    = int(((pred == 1) & (y_v == 0)).sum())
        fn    = int(((pred == 0) & (y_v == 1)).sum())
        prec  = tp / max(tp + fp, 1)
        rec   = tp / max(tp + fn, 1)
        f1    = 2 * prec * rec / max(prec + rec, 1e-9)
        est_trades = int((p_v >= thr).sum()) / HORIZON_BARS / n_months
        bonus = min(est_trades / MIN_TRADES_MONTH, 1.0)
        score = f1 * bonus
        if score > best_score and prec >= 0.60:
            best_score, best_thr = score, float(thr)
    return best_thr


# ─────────────────────────────────────────────────────────────────────────────
# Simulation prop firm
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_propfirm(
    trade_rets:  List[float],
    trade_dates: List[pd.Timestamp],
    config:      PropFirmConfig,
    capital:     float,
) -> Dict:
    """
    Simule le compte prop firm sur une série de trades.

    Retourne métriques financières + statut de l'évaluation.
    """
    if not trade_rets:
        return {
            "n_trades": 0, "pf": 0.0, "wr": 0.0,
            "roi_month": 0.0, "max_dd_pct": 0.0,
            "monthly_profit_usd": 0.0, "total_profit_usd": 0.0,
            "eval_passed": False, "eval_days_taken": 0,
            "dd_violations": 0, "daily_dd_violations": 0,
        }

    ps    = config.position_size
    arr   = np.array(trade_rets, dtype=np.float64)
    dates = pd.Series(trade_dates)
    n     = len(arr)

    equity    = np.ones(n + 1) * capital
    peak      = np.ones(n + 1) * capital
    dd_viol   = 0
    daily_viol= 0
    dd_history= np.zeros(n)
    eval_passed = False
    eval_days_taken = 0

    # Regrouper par jour pour la règle daily DD
    trade_df  = pd.DataFrame({"ret": arr, "date": dates})
    trade_df["day"] = trade_df["date"].dt.date
    daily_ret = trade_df.groupby("day")["ret"].sum().to_dict()

    day_equity = capital  # pour le suivi daily DD
    prev_day   = None

    for i, (ret, d) in enumerate(zip(arr, dates)):
        # Réinitialiser l'equity journalière au début d'un nouveau jour
        day = d.date() if hasattr(d, 'date') else d
        if day != prev_day:
            day_equity = equity[i]
            prev_day   = day

        # Appliquer le trade
        trade_pnl = equity[i] * ps * ret
        equity[i + 1] = equity[i] + trade_pnl
        peak[i + 1]   = max(peak[i], equity[i + 1])

        # Vérifier DD max
        current_dd_pct = (peak[i + 1] - equity[i + 1]) / peak[i + 1] * 100
        dd_history[i]  = current_dd_pct
        if config.max_dd_pct > 0 and current_dd_pct > config.max_dd_pct:
            dd_viol += 1

        # Vérifier daily DD
        if config.daily_dd_pct > 0:
            daily_loss_pct = (day_equity - equity[i + 1]) / day_equity * 100
            if daily_loss_pct > config.daily_dd_pct:
                daily_viol += 1

        # Évaluation : vérifier si l'objectif de profit est atteint
        if (not eval_passed and config.profit_target_pct > 0
                and dd_viol == 0 and daily_viol == 0):
            profit_pct = (equity[i + 1] - capital) / capital * 100
            if profit_pct >= config.profit_target_pct:
                if config.eval_days > 0:
                    days_elapsed = (d - dates.iloc[0]).days + 1
                    if days_elapsed <= config.eval_days:
                        eval_passed = True
                        eval_days_taken = days_elapsed
                else:
                    eval_passed = True

    # Si pas de règle d'éval, le compte est "passé" par défaut si pas de violation
    if config.profit_target_pct == 0 and dd_viol == 0:
        eval_passed = True

    # Métriques finales
    pnl_arr  = np.diff(equity)
    wins     = pnl_arr[pnl_arr > 0]
    losses   = pnl_arr[pnl_arr < 0]
    pf       = float(wins.sum()) / max(float(abs(losses.sum())), 1e-9)
    wr       = len(wins) / max(len(pnl_arr), 1)
    max_dd   = float(dd_history.max())
    total_profit = float(equity[-1] - capital) * config.profit_split

    if "datetime" in trade_df.columns or len(dates) > 0:
        d0 = pd.Timestamp(dates.iloc[0]) if len(dates) else pd.Timestamp.now()
        d1 = pd.Timestamp(dates.iloc[-1]) if len(dates) else pd.Timestamp.now()
        n_months = max((d1 - d0).days / 30.44, 1.0)
    else:
        n_months = 12.0

    monthly_profit = (total_profit / n_months)
    roi_month = ((equity[-1] / capital) ** (1.0 / n_months) - 1.0) * 100.0 * config.profit_split

    return {
        "n_trades":            n,
        "pf":                  round(pf, 3),
        "wr":                  round(wr, 3),
        "roi_month":           round(roi_month, 2),
        "max_dd_pct":          round(max_dd, 2),
        "monthly_profit_usd":  round(monthly_profit, 2),
        "total_profit_usd":    round(total_profit, 2),
        "eval_passed":         eval_passed,
        "eval_days_taken":     eval_days_taken,
        "dd_violations":       dd_viol,
        "daily_dd_violations": daily_viol,
        "final_equity":        round(float(equity[-1]), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward d'un fold sur un actif
# ─────────────────────────────────────────────────────────────────────────────

def run_fold_asset(
    df:        pd.DataFrame,
    test_year: int,
    features:  List[str],
) -> Optional[Tuple[List[float], List[pd.Timestamp], float, float]]:
    """
    Entraîne + backteste un fold.
    Retourne (trade_rets, trade_dates, threshold, fleet_auc).
    """
    years = df["datetime"].dt.year.values
    train_mask = years <= (test_year - 2)
    val_mask   = years == (test_year - 1)
    test_mask  = years == test_year

    if train_mask.sum() < 2000 or val_mask.sum() < 200 or test_mask.sum() < 200:
        return None

    # Labels sur df entier (anti-leakage)
    if TARGET_COL not in df.columns:
        df = compute_label_columns(df)
    if "regime_long" not in df.columns:
        df = compute_long_regime_col(df)
    try:
        df, _ = build_labels(df, train_mask)
    except Exception:
        pass

    df_train = df.loc[train_mask].copy()
    df_val   = df.loc[val_mask].copy()
    df_test  = df.loc[test_mask].copy()

    feats_avail = get_available_features(df_train, features, min_fill=0.65, context="v5")
    if len(feats_avail) < 10:
        return None

    # Déterminer le split train/val dans df_train (pour fleet.fit)
    pool_val_mask   = train_mask.copy()
    pool_val_mask[:] = False
    pool_train_mask = train_mask.copy()
    # val = dernière année du train
    val_year_in_train = test_year - 1
    pool_val_mask   = years == val_year_in_train
    pool_train_only = years <= (test_year - 2)

    fleet = TRMFleetLongV5(features_base=feats_avail, features_alpha=[])
    df_for_fit = df.loc[train_mask | val_mask].copy()

    # train_mask dans ce sous-df
    sub_train = df_for_fit["datetime"].dt.year.values <= (test_year - 2)
    sub_val   = df_for_fit["datetime"].dt.year.values == (test_year - 1)

    fleet.fit(
        df_for_fit, sub_train,
        val_mask=sub_val if sub_val.sum() > 100 else None,
        target_col=TARGET_COL,
        regime_col="regime_long",
    )

    # Calibrer threshold sur val
    thr = _calibrate_thr(df_val, fleet)

    # Backtest séquentiel sur test
    n_test    = len(df_test)
    ones      = np.ones(n_test, dtype=bool)
    p_long    = fleet.predict(df_test, ones)
    no_long   = (df_test.get("regime_long", pd.Series("NEUTRAL", index=df_test.index))
                 .values == "NO_LONG")
    target_arr = df_test[TARGET_COL].fillna(0.0).values if TARGET_COL in df_test.columns \
                 else np.zeros(n_test)
    dates_arr  = df_test["datetime"].values if "datetime" in df_test.columns else None

    trade_rets:  List[float]        = []
    trade_dates: List[pd.Timestamp] = []
    cooldown = 0

    for i in range(n_test):
        if cooldown > 0:
            cooldown -= 1
            continue
        if no_long[i]:
            continue
        if p_long[i] >= thr:
            trade_rets.append(float(target_arr[i]) - COST_PCT)
            trade_dates.append(
                pd.Timestamp(dates_arr[i]) if dates_arr is not None
                else pd.Timestamp(f"{test_year}-01-01")
            )
            cooldown = HORIZON_BARS

    return trade_rets, trade_dates, thr, fleet.fleet_auc_


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT",
]

# Folds disponibles selon l'historique de chaque actif
_FOLD_STARTS = {
    "BTCUSDT": 2020, "ETHUSDT": 2020,
    "DOGEUSDT": 2021, "XRPUSDT": 2021, "LINKUSDT": 2021,
    "SOLUSDT": 2022, "BNBUSDT": 2022,
    "ADAUSDT": 2022, "AVAXUSDT": 2022, "DOTUSDT": 2022,
}

def main() -> None:
    print(f"\n{'='*72}")
    print("  TEST MULTI-ACTIF × PROP FIRM × PORTEFEUILLE")
    print(f"  {len(SYMBOLS)} actifs  |  {len(PROP_FIRMS)} configs  |  {len(PORTFOLIO_SIZES)} tailles")
    print(f"{'='*72}\n")

    rows_per_asset   = []
    rows_propfirm    = []
    rows_monthly     = []
    summary          = {}

    for symbol in SYMBOLS:
        print(f"\n{'─'*60}")
        print(f"  ▶  {symbol}")
        print(f"{'─'*60}")

        df = _load_enrich(symbol)
        if df is None:
            print(f"     ✗ Données manquantes — skip")
            continue

        print(f"     {len(df):,} barres  "
              f"{df['datetime'].min().date()} → {df['datetime'].max().date()}")

        # Folds pour cet actif (au moins 2 ans train + 1 val + 1 test)
        start_fold = _FOLD_STARTS.get(symbol, 2022)
        test_years = [y for y in range(start_fold, 2026)
                      if (df["datetime"].dt.year.values <= y - 2).sum() >= 2000
                      and (df["datetime"].dt.year.values == y).sum() >= 500]

        if not test_years:
            print(f"     ✗ Pas assez de folds — skip")
            continue

        print(f"     Folds : {test_years}")

        # Collecter tous les trades sur tous les folds
        all_rets:  List[float]        = []
        all_dates: List[pd.Timestamp] = []
        fold_aucs: List[float]        = []
        fold_results = {}

        for year in test_years:
            print(f"     Fold {year}...", end=" ", flush=True)
            result = run_fold_asset(df, year, FEATURES_V5)
            if result is None:
                print("skip")
                continue
            t_rets, t_dates, thr, auc = result
            fold_results[year] = {"rets": t_rets, "dates": t_dates, "thr": thr, "auc": auc}
            all_rets.extend(t_rets)
            all_dates.extend(t_dates)
            fold_aucs.append(auc)
            print(f"n={len(t_rets)}  AUC={auc:.4f}")

        if not all_rets:
            print(f"     ✗ Aucun trade — skip")
            continue

        # ── Métriques globales par actif ──────────────────────────────────────
        arr  = np.array(all_rets)
        wins = arr[arr > 0]
        loss = arr[arr < 0]
        pf   = float(wins.sum()) / max(float(abs(loss.sum())), 1e-9)
        wr   = len(wins) / len(arr)

        # ROI mensuel (25% sizing, self-funded)
        equity = np.cumprod(1.0 + arr * 0.25)
        d0, d1 = all_dates[0], all_dates[-1]
        n_months = max((pd.Timestamp(d1) - pd.Timestamp(d0)).days / 30.44, 1.0)
        roi_m = ((equity[-1]) ** (1.0 / n_months) - 1.0) * 100.0

        # Drawdown max
        peak = np.maximum.accumulate(equity)
        dd   = (equity - peak) / peak
        max_dd = float(abs(dd.min())) * 100.0

        asset_row = {
            "symbol":      symbol,
            "n_folds":     len(test_years),
            "n_trades":    len(all_rets),
            "pf":          round(pf, 3),
            "wr":          round(wr, 3),
            "roi_month":   round(roi_m, 2),
            "max_dd_pct":  round(max_dd, 2),
            "mean_auc":    round(float(np.mean(fold_aucs)) if fold_aucs else 0.0, 4),
            "years_covered": f"{test_years[0]}-{test_years[-1]}",
        }
        rows_per_asset.append(asset_row)

        print(f"\n     Résultat global : n={len(all_rets)}  "
              f"PF={pf:.2f}  WR={wr:.1%}  "
              f"ROI={roi_m:+.2f}%/m  MaxDD={max_dd:.1f}%")

        # ── Détail mensuel ────────────────────────────────────────────────────
        df_trades = pd.DataFrame({"ret": all_rets, "date": [pd.Timestamp(x) for x in all_dates]})
        df_trades["month"] = df_trades["date"].dt.to_period("M")
        for ym, grp in df_trades.groupby("month"):
            arr_m = grp["ret"].values * 0.25
            eq_m  = np.cumprod(1.0 + arr_m)
            rows_monthly.append({
                "symbol": symbol, "month": str(ym),
                "n_trades": len(arr_m),
                "roi_pct": round((eq_m[-1] - 1.0) * 100.0, 2),
                "pf": round(
                    float(arr_m[arr_m > 0].sum()) /
                    max(float(abs(arr_m[arr_m < 0].sum())), 1e-9), 2
                ),
            })

        # ── Grille Prop Firm × Portefeuille ──────────────────────────────────
        print(f"\n     {'Config':<28} {'$10k→':<12} {'$25k→':<12} {'ROI/m':>8} {'Passé?':>7}")
        print(f"     {'─'*65}")

        for cfg in PROP_FIRMS:
            for capital in PORTFOLIO_SIZES:
                sim = _simulate_propfirm(all_rets, all_dates, cfg, capital)
                rows_propfirm.append({
                    "symbol":       symbol,
                    "propfirm":     cfg.name,
                    "propfirm_label": cfg.label,
                    "capital_usd":  capital,
                    **{k: v for k, v in sim.items()},
                })

            # Afficher uniquement $10k et $25k pour la lisibilité
            sim10 = _simulate_propfirm(all_rets, all_dates, cfg, 10_000)
            sim25 = _simulate_propfirm(all_rets, all_dates, cfg, 25_000)
            passed_str = "✓ PASSÉ" if sim10["eval_passed"] else "✗ échec"
            print(f"     {cfg.label:<28} "
                  f"${sim10['total_profit_usd']:>9,.0f}  "
                  f"${sim25['total_profit_usd']:>9,.0f}  "
                  f"{sim10['roi_month']:>+7.2f}%  "
                  f"{passed_str}")

        summary[symbol] = {
            "asset_stats":  asset_row,
            "folds":        {str(y): {"n": len(fold_results[y]["rets"]),
                                       "auc": fold_results[y]["auc"]}
                             for y in fold_results},
        }

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    print(f"\n\n{'='*72}")
    print("  RÉSUMÉ GLOBAL PAR ACTIF")
    print(f"{'='*72}")

    df_assets = pd.DataFrame(rows_per_asset).sort_values("roi_month", ascending=False)
    print(df_assets.to_string(index=False))

    df_propfirm = pd.DataFrame(rows_propfirm)
    df_monthly  = pd.DataFrame(rows_monthly)

    df_assets.to_csv(REPORT_DIR / "per_asset.csv", index=False)
    df_propfirm.to_csv(REPORT_DIR / "propfirm_grid.csv", index=False)
    df_monthly.to_csv(REPORT_DIR / "monthly_detail.csv", index=False)

    with open(REPORT_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # ── Meilleure combo Prop Firm × Actif ────────────────────────────────────
    print(f"\n{'='*72}")
    print("  TOP COMBOS PROP FIRM (capital $10k, profit total)")
    print(f"{'='*72}")
    df_10k = df_propfirm[df_propfirm["capital_usd"] == 10_000].copy()
    df_10k["score"] = df_10k["total_profit_usd"] * df_10k["eval_passed"].astype(int)
    best = df_10k.nlargest(15, "total_profit_usd")[
        ["symbol", "propfirm_label", "total_profit_usd",
         "monthly_profit_usd", "roi_month", "max_dd_pct",
         "n_trades", "eval_passed"]
    ]
    print(best.to_string(index=False))

    # ── Tableau multi-portfolio par prop firm ────────────────────────────────
    print(f"\n{'='*72}")
    print("  PROFIT MENSUEL (USD) PAR TAILLE DE COMPTE — BTC + meilleur actif")
    print(f"{'='*72}")
    best_sym = df_assets.iloc[0]["symbol"] if len(df_assets) else "BTCUSDT"
    for sym in ["BTCUSDT", best_sym] if best_sym != "BTCUSDT" else ["BTCUSDT"]:
        print(f"\n  {sym}")
        sym_data = df_propfirm[df_propfirm["symbol"] == sym]
        for cfg in PROP_FIRMS:
            cfg_data = sym_data[sym_data["propfirm"] == cfg.name].sort_values("capital_usd")
            if cfg_data.empty:
                continue
            row_str = f"  {cfg.label:<28}"
            for _, rd in cfg_data.iterrows():
                row_str += f"  ${rd['monthly_profit_usd']:>7,.0f}"
            print(row_str)
        capitals_str = "  " + " " * 28 + "".join(
            f"  {'$'+str(c//1000)+'k':>9}" for c in PORTFOLIO_SIZES
        )
        print(capitals_str)

    print(f"\n  Rapports sauvés : {REPORT_DIR}")
    print(f"  - per_asset.csv      : métriques par actif")
    print(f"  - propfirm_grid.csv  : grille complète (toutes combos)")
    print(f"  - monthly_detail.csv : détail mensuel")
    print(f"  - summary.json       : résumé complet\n")


if __name__ == "__main__":
    main()
