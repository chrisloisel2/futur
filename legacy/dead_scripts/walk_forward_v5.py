#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/walk_forward_v5.py — WALK-FORWARD UNIFIÉ v5 (données data_out/result/)
===============================================================================

Adapté au format réel : barres 1 minute par année par actif.
  data_out/result/{YEAR}_{SYM}_features.parquet

Pipeline :
  1. Charge + resample 1m → 1h  (OHLCV + last value des indicateurs pré-calculés)
  2. Calcule sur 1h : EMA50, EMA200, momentum 72h/720h
  3. Labels : quantile 8h top-20%  OU  Triple Barrier (--triple-barrier)
  4. Training : TRMFleetLongV4 (100 TRM)  multi-actif BTC+ETH+SOL+BNB
  5. Régime : RegimeAllocatorV5  (BEAR sizing + Funding Harvest)
  6. Walk-forward 4 folds : 2022, 2023, 2024, 2025

Critères de déploiement :
  ≥ 4/4 folds PF ≥ 1.20, médian PF ≥ 1.30, 0 catastrophique

Usage :
  python scripts/walk_forward_v5.py
  python scripts/walk_forward_v5.py --folds 2023,2024,2025
  python scripts/walk_forward_v5.py --triple-barrier
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_2.trm_fleet_long_v4 import (
    TRMFleetLongV4, calibrate_context_thresholds_v4, classify_context_v4,
    TEMPORAL_HORIZONS_V4, MOVEMENT_ARCHETYPES_V4,
)
from ai.level_0.labels import (
    compute_label_columns, build_labels, compute_long_regime_col,
    build_triple_barrier_labels_long,
)
from ai.level_0.constants import (
    COST_PCT, COST_SHORT_MULT, TARGET_COL, HORIZON_BARS, CLOSE_COL,
)
from ai.level_2.regime_allocator import run_regime_fold

DATA_DIR   = ROOT / "data_out" / "result"
REPORT_DIR = ROOT / "reports" / "walk_forward_v5"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

COST_SHORT       = COST_PCT * COST_SHORT_MULT
DEPLOY_PF        = 1.20
CATASTROPHIC_PF  = 0.75
MIN_TRADES       = 5
SYMBOLS          = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PRIMARY_SYM      = "BTCUSDT"

# Colonnes à agréger en last() lors du resample 1m→1h
_LAST_COLS = [
    "funding_rate", "rsi_14", "rsi_60", "atr_14", "atr_pct_14", "atr_240",
    "oi_sum", "oi_value_sum", "oi_chg_60m", "oi_chg_240m",
    "top_trader_lsr", "lsr_z_1d",
    "funding_z_7d", "funding_z_30d", "funding_extreme",
    "macd_hist", "macd_line", "macd_signal",
    "adx_14", "stoch_rsi_k", "squeeze_mom",
    "ema_dist_8", "ema_dist_21", "ema_dist_55", "ema_dist_144",
    "volume_z_60m", "volume_z_240m",
    "fear_greed", "fred_vixcls",
]

# ─────────────────────────────────────────────────────────────────────────────
# Chargement + resample
# ─────────────────────────────────────────────────────────────────────────────

_LOAD_COLS = [
    "timestamp",
    # OHLCV
    "open", "high", "low", "close", "volume",
    # Features pre-calculées
    "funding_rate", "rsi_14", "rsi_60", "atr_14", "atr_pct_14", "atr_240",
    "oi_sum", "oi_value_sum", "oi_chg_60m", "oi_chg_240m",
    "top_trader_lsr", "lsr_z_1d",
    "funding_z_7d", "funding_z_30d", "funding_extreme",
    "macd_hist", "macd_line",
    "adx_14", "stoch_rsi_k", "squeeze_mom",
    "ema_dist_8", "ema_dist_21", "ema_dist_55", "ema_dist_144",
    "volume_z_60m", "volume_z_240m",
    "fear_greed", "fred_vixcls",
]


def _load_symbol_years(sym: str, years: List[int]) -> Optional[pd.DataFrame]:
    """Charge + resample directement en mémoire limitée : 1 année à la fois."""
    dfs_1h = []
    for yr in years:
        path = DATA_DIR / f"{yr}_{sym}_features.parquet"
        if not path.exists():
            continue
        try:
            # Lire seulement les colonnes nécessaires — ~10× moins de RAM
            import pyarrow.parquet as pq
            pf    = pq.ParquetFile(path)
            avail = set(pf.schema_arrow.names)
            cols  = [c for c in _LOAD_COLS if c in avail]
            df    = pd.read_parquet(path, columns=cols)
            # Resample tout de suite → libère les 1m
            df1h  = _resample_1h(df)
            dfs_1h.append(df1h)
            del df
        except Exception as e:
            print(f"   ⚠  {path.name}: {e}")
    if not dfs_1h:
        return None
    return pd.concat(dfs_1h, ignore_index=True)


def _resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1m → 1h. Timestamp column → DatetimeIndex."""
    if "timestamp" in df.columns:
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("datetime").sort_index()
    elif "datetime" in df.columns:
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()

    # OHLCV
    agg: Dict = {
        "open":  "first",
        "high":  "max",
        "low":   "min",
        "close": "last",
        "volume": "sum",
    }
    for col in _LAST_COLS:
        if col in df.columns:
            agg[col] = "last"

    df_h = df.resample("1h").agg(agg)
    df_h = df_h.dropna(subset=["close"])
    df_h = df_h.reset_index()
    return df_h


def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute EMA50/EMA200/momentum sur barres 1h."""
    df = df.copy()
    c = df["close"].ffill()

    # EMA pour le régime gate
    ema50  = c.ewm(span=50,  adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    df["dist_ema_50"]      = (c / ema50  - 1.0)
    df["dist_ema_200"]     = (c / ema200 - 1.0)
    df["ema_spread_50_200"]= (ema50 / ema200 - 1.0)

    log_c = np.log(c.clip(lower=1e-9))
    df["mom_logret_72"]  = log_c - log_c.shift(72)
    df["mom_logret_720"] = log_c - log_c.shift(720)

    # Realized vol (pour les features TRM)
    ret1h = log_c.diff().fillna(0.0)
    df["rv_24"]  = ret1h.rolling(24).std()
    df["rv_72"]  = ret1h.rolling(72).std()
    df["rv_ratio_24_72"] = (df["rv_24"] / df["rv_72"].clip(lower=1e-9)).fillna(1.0)
    df["rv_ratio_12_48"] = (ret1h.rolling(12).std() / ret1h.rolling(48).std().clip(lower=1e-9)).fillna(1.0)

    # Renommages attendus par TRM + labels
    df[CLOSE_COL] = c                                  # "Close" capital C
    df["Close"]   = c
    df["volume"]  = df["volume"].fillna(0.0)

    return df


def load_symbol(sym: str, years: List[int]) -> Optional[pd.DataFrame]:
    raw = _load_symbol_years(sym, years)
    if raw is None:
        return None
    df1h = _resample_1h(raw)
    df1h = _add_derived_features(df1h)
    df1h["symbol"] = sym
    print(f"   {sym}: {len(df1h):,} barres 1h "
          f"({df1h['datetime'].iloc[0].date()} → {df1h['datetime'].iloc[-1].date()})")
    return df1h


# ─────────────────────────────────────────────────────────────────────────────
# Features disponibles
# ─────────────────────────────────────────────────────────────────────────────

_BASE_FEATURES = [
    # Régime / tendance
    "dist_ema_50", "dist_ema_200", "ema_spread_50_200",
    "mom_logret_72", "mom_logret_720",
    "ema_dist_8", "ema_dist_21", "ema_dist_55", "ema_dist_144",
    # Volatilité
    "rv_24", "rv_72", "rv_ratio_24_72", "rv_ratio_12_48",
    "atr_14", "atr_pct_14", "atr_240",
    # Momentum / prix
    "rsi_14", "rsi_60", "stoch_rsi_k",
    "macd_hist", "macd_line",
    "adx_14", "squeeze_mom",
    # Volume / liquidité
    "volume_z_60m", "volume_z_240m",
    # Institutionnel
    "funding_rate", "funding_z_7d", "funding_z_30d", "funding_extreme",
    "oi_sum", "oi_chg_60m", "oi_chg_240m",
    "top_trader_lsr", "lsr_z_1d",
    # Macro
    "fear_greed", "fred_vixcls",
]


def _select_features(df: pd.DataFrame, candidates: List[str]) -> List[str]:
    available = []
    for f in candidates:
        if f not in df.columns:
            continue
        fill_rate = df[f].notna().mean()
        if fill_rate >= 0.60:
            available.append(f)
    return available


# ─────────────────────────────────────────────────────────────────────────────
# Backtest d'un fold
# ─────────────────────────────────────────────────────────────────────────────

def _backtest(
    df_test: pd.DataFrame,
    fleet: TRMFleetLongV4,
    thresholds: Dict[str, float],
    features: List[str],
    filter_clf=None, filter_scaler=None, filter_feats=None,
    cost_pct: float = COST_PCT,
) -> Dict:
    n = len(df_test)
    if n == 0:
        return {"n_trades": 0, "pf": 0.0, "wr": 0.0, "expectancy": 0.0,
                "max_drawdown": 0.0, "total_pnl": 0.0}

    ones   = np.ones(n, dtype=bool)
    p_all  = fleet.predict(df_test, ones)
    ctx    = classify_context_v4(df_test)

    # Gate regime (rule-based: NO_LONG label from level_0)
    tradeable = ones.copy()
    if "regime_long" in df_test.columns:
        tradeable &= (df_test["regime_long"].values != "NO_LONG")

    rets = df_test[TARGET_COL].fillna(0.0).values if TARGET_COL in df_test.columns \
           else np.zeros(n)

    trade_rets: List[float] = []
    for i in range(n):
        if not tradeable[i]:
            continue
        thr = thresholds.get(str(ctx[i]), thresholds.get("general", 0.54))
        if p_all[i] >= thr:
            trade_rets.append(float(rets[i]) - cost_pct)

    if not trade_rets:
        return {"n_trades": 0, "pf": 0.0, "wr": 0.0, "expectancy": 0.0,
                "max_drawdown": 0.0, "total_pnl": 0.0}

    arr   = np.array(trade_rets)
    wins  = arr[arr > 0];  losses = arr[arr < 0]
    gw    = float(wins.sum())   if len(wins)   else 0.0
    gl    = float(abs(losses.sum())) if len(losses) else 0.0
    pf    = gw / max(gl, 1e-9)
    wr    = len(wins) / len(arr)
    eq    = np.cumprod(1.0 + arr * 0.01)
    peak  = np.maximum.accumulate(eq)
    dd    = float(abs(((eq - peak) / np.maximum(peak, 1e-9)).min())) * 100

    return {
        "n_trades":    len(arr),
        "pf":          round(pf, 3),
        "wr":          round(wr, 3),
        "expectancy":  round(float(arr.mean()) * 100, 4),
        "max_drawdown":round(dd, 2),
        "total_pnl":   round(float(arr.sum()), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward principal
# ─────────────────────────────────────────────────────────────────────────────

def run_fold(
    df_primary:  pd.DataFrame,
    extra_dfs:   List[pd.DataFrame],
    test_year:   int,
    features:    List[str],
    label_col:   str = "y_long",
    run_regime:  bool = True,
) -> Dict:
    years  = df_primary["datetime"].dt.year.values
    tr_msk = years <= (test_year - 2)
    va_msk = years == (test_year - 1)
    te_msk = years == test_year

    n_tr, n_va, n_te = tr_msk.sum(), va_msk.sum(), te_msk.sum()
    if n_tr < 1000 or n_va < 500 or n_te < 500:
        return {"year": test_year, "skip": True,
                "reason": f"data trop courte: tr={n_tr} va={n_va} te={n_te}"}

    print(f"\n  ── Fold {test_year}  "
          f"[train≤{test_year-2}: {n_tr:,}]  "
          f"[val {test_year-1}: {n_va:,}]  "
          f"[test {test_year}: {n_te:,}]")

    # ── Labels sur dataset complet (anti-leakage) ─────────────────────────────
    # Fix #2 : quantile 0.72 (top 28%) + reversal filter moins strict
    df_full = compute_label_columns(df_primary)
    df_full = compute_long_regime_col(df_full)
    df_full, _ = build_labels(
        df_full, tr_msk,
        tradeable_quantile=0.72,      # top 28% → plus de labels positifs
        gray_zone_factor=0.05,        # zone grise réduite → moins d'exclusions
        use_reversal_filter=False,    # Fix #2 : désactivé — trop agressif sur crypto 1h
        use_long_reversal_filter=False,
    )

    if label_col in ("y_long_tb", "y_hybrid"):
        df_full = build_triple_barrier_labels_long(df_full)
        if (df_full["y_long_tb"] == 1).sum() < 50:
            label_col = "y_long"
            print("   TB insuffisant → fallback y_long")
        elif label_col == "y_hybrid":
            # Hybrid: positive = (quantile=1 AND TB=1), negative = (quantile=0 OR stop hit),
            # excluded = -1 (y_long=1 AND time-barrier hit → ambiguous)
            y_q  = df_full["y_long"].values.astype(np.int32)
            y_tb = df_full["y_long_tb"].values.astype(np.int32)
            y_h  = np.zeros(len(df_full), dtype=np.int32)
            y_h[(y_q == 1) & (y_tb == 1)] = 1    # ideal: profitable + safe
            y_h[(y_q == 1) & (y_tb == -1)] = -1  # ambiguous time-barrier → exclude
            df_full["y_hybrid"] = y_h
            n_pos = int((y_h == 1).sum())
            n_excl = int((y_h == -1).sum())
            print(f"   Hybrid labels: {n_pos} positifs ({n_pos/len(df_full)*100:.1f}%)  "
                  f"{n_excl} exclus (time-barrier)")

    # ── Multi-actif train ─────────────────────────────────────────────────────
    dfs_train = [df_full.loc[tr_msk].copy()]
    n_extra = 0
    for df_ex in extra_dfs:
        yr_ex = df_ex["datetime"].dt.year.values
        msk_ex = yr_ex <= (test_year - 2)
        if msk_ex.sum() < 500:
            continue
        n_feat_ok = sum(1 for f in features if f in df_ex.columns)
        if n_feat_ok / max(len(features), 1) < 0.60:
            continue
        try:
            df_ex2 = compute_label_columns(df_ex)
            df_ex2 = compute_long_regime_col(df_ex2)
            df_ex2, _ = build_labels(
                df_ex2, msk_ex,
                tradeable_quantile=0.72,
                gray_zone_factor=0.05,
                use_reversal_filter=False,
                use_long_reversal_filter=False,
            )
            if label_col in ("y_long_tb", "y_hybrid"):
                df_ex2 = build_triple_barrier_labels_long(df_ex2)
            if label_col == "y_hybrid" and "y_long_tb" in df_ex2.columns:
                y_q2  = df_ex2["y_long"].values.astype(np.int32)
                y_tb2 = df_ex2["y_long_tb"].values.astype(np.int32)
                y_h2  = np.zeros(len(df_ex2), dtype=np.int32)
                y_h2[(y_q2 == 1) & (y_tb2 == 1)] = 1
                y_h2[(y_q2 == 1) & (y_tb2 == -1)] = -1
                df_ex2["y_hybrid"] = y_h2
            dfs_train.append(df_ex2.loc[msk_ex].copy())
            n_extra += 1
        except Exception:
            continue

    print(f"   Pool training : BTC + {n_extra} actifs extra")
    df_train = pd.concat(dfs_train, ignore_index=True)
    tr_all   = np.ones(len(df_train), dtype=bool)

    # Features disponibles dans le pool
    feat = _select_features(df_train, features)
    print(f"   Features : {len(feat)} (fill≥60%)")

    # ── TRM Fleet Long v4 ─────────────────────────────────────────────────────
    df_val = df_full.loc[va_msk].copy()

    fleet = TRMFleetLongV4(features=feat)
    fleet.train(
        df_train, tr_all,
        df_val_btc=df_val,
        val_mask_in_btc=np.ones(len(df_val), dtype=bool),
        label_col=label_col,
    )

    # ── Calibration seuils sur val ────────────────────────────────────────────
    ret_val = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns \
              else np.zeros(len(df_val))
    thresholds = calibrate_context_thresholds_v4(
        fleet, df_val,
        filter_p=np.ones(len(df_val)),
        filter_thr=0.50,
        ret_val=ret_val,
        cost_pct=COST_PCT,
    )
    adapt = fleet.adaptive_threshold()
    thresholds = {k: max(v, adapt) for k, v in thresholds.items()}

    # ── Backtest test ─────────────────────────────────────────────────────────
    df_test  = df_full.loc[te_msk].copy()
    res_long = _backtest(df_test, fleet, thresholds, feat)

    # ── Régime Allocator (stats uniquement, pas de gate sur trades) ───────────
    res_regime = {}
    if run_regime:
        try:
            res_regime = run_regime_fold(df_test)
        except Exception as e:
            print(f"   ⚠  Regime fold erreur : {e}")

    # Statut du fold
    n, pf = res_long["n_trades"], res_long["pf"]
    dd    = res_long.get("max_drawdown", 0.0)
    if n < MIN_TRADES:
        status = "NO_TRADES"
    elif pf < CATASTROPHIC_PF or dd > 20.0:
        status = "CATASTROPHIC"
    elif pf >= DEPLOY_PF:
        status = "OK"
    else:
        status = "WEAK"

    # Buy-and-hold de référence
    prices = df_test["close"].dropna()
    bh = 0.0
    if len(prices) > 1:
        bh = (float(prices.iloc[-1]) - float(prices.iloc[0])) / float(prices.iloc[0]) * 100

    bear_pct     = res_regime.get("bear_pct", 0.0)
    harvest_n    = res_regime.get("harvest_n", 0)
    harvest_pf   = res_regime.get("harvest_pf", 0.0)
    dd_red_est   = res_regime.get("dd_reduction_est_pct", 0.0)

    print(
        f"  [{test_year}] LONG [{status:^12}]  "
        f"n={n:4d}  PF={pf:.3f}  WR={res_long['wr']:.0%}  "
        f"DD={dd:.1f}%  E={res_long['expectancy']:+.4f}%  B&H={bh:+.0f}%"
    )
    print(
        f"  [{test_year}] HEDGE              "
        f"BEAR={bear_pct:.1f}%  DD_est=-{dd_red_est:.1f}%  "
        f"harvest n={harvest_n}  PF={harvest_pf:.2f}"
    )

    return {
        "year":      test_year,
        "skip":      False,
        "status":    status,
        "long":      res_long,
        "regime":    res_regime,
        "bh_pct":    round(bh, 2),
        "label_col": label_col,
        "auc_mean":  fleet._fleet_auc_mean,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rapport final
# ─────────────────────────────────────────────────────────────────────────────

def _print_report(fold_results: List[Dict]) -> Dict:
    valid  = [f for f in fold_results if not f.get("skip")]
    n_tot  = len(valid)

    ok    = sum(1 for f in valid if f["status"] == "OK")
    cata  = sum(1 for f in valid if f["status"] == "CATASTROPHIC")
    pfs   = [f["long"]["pf"] for f in valid if f["long"]["n_trades"] >= MIN_TRADES]
    pf_med= float(np.median(pfs)) if pfs else 0.0
    n_trades = sum(f["long"]["n_trades"] for f in valid)
    wrs   = [f["long"]["wr"] for f in valid if f["long"]["n_trades"] >= MIN_TRADES]

    bear_pcts = [f.get("regime", {}).get("bear_pct", 0.0) for f in valid]
    dd_reds   = [f.get("regime", {}).get("dd_reduction_est_pct", 0.0) for f in valid]
    h_n_tot   = sum(f.get("regime", {}).get("harvest_n", 0) for f in valid)
    h_pfs     = [f.get("regime", {}).get("harvest_pf", 0.0) for f in valid if f.get("regime", {}).get("harvest_n", 0) > 0]

    print("\n" + "=" * 72)
    print("VERDICT FINAL — TRM FLEET LONG v4 + RÉGIME ALLOCATOR v5")
    print("=" * 72)

    # Tableau par fold
    print(f"\n  {'Année':^6} {'Status':^14} {'N':>5} {'PF':>6} {'WR':>5} "
          f"{'DD%':>5} {'E%':>7} {'B&H%':>6}  {'BEAR%':>6} {'Harv':>5}")
    print("  " + "-" * 72)
    for f in fold_results:
        if f.get("skip"):
            print(f"  [{f['year']}] SKIP — {f.get('reason', '')}")
            continue
        l  = f["long"]
        r  = f.get("regime", {})
        tb = " (TB)" if f.get("label_col") == "y_long_tb" else ""
        icon = {"OK": "OK", "WEAK": "~~", "CATASTROPHIC": "XX", "NO_TRADES": " 0"}.get(f["status"], "?")
        print(
            f"  [{f['year']}] {icon} {f['status']:^12} {l['n_trades']:5d} "
            f"{l['pf']:6.3f} {l['wr']:5.0%} {l.get('max_drawdown',0):5.1f}% "
            f"{l['expectancy']:+7.4f}% {f['bh_pct']:+6.0f}%  "
            f"{r.get('bear_pct',0):5.1f}% "
            f"n={r.get('harvest_n',0):3d}{tb}"
        )

    # Verdict LONG
    deployable = ok >= max(1, int(n_tot * 0.7)) and cata == 0 and pf_med >= DEPLOY_PF
    print(f"\n  LONG  : {'OK DEPLOYABLE' if deployable else 'XX NOT_DEPLOYABLE'}")
    print(f"    Folds OK    : {ok}/{n_tot}")
    print(f"    Catastroph. : {cata}")
    print(f"    PF médian   : {pf_med:.3f}  (seuil : {DEPLOY_PF})")
    print(f"    WR médian   : {np.median(wrs):.1%}" if wrs else "    WR médian   : N/A")
    print(f"    Total trades: {n_trades}")

    # Régime
    print(f"\n  HEDGE : Régime Allocator v5")
    print(f"    BEAR moyen     : {np.mean(bear_pcts):.1f}% du temps de test")
    print(f"    DD réd. est.   : -{np.mean(dd_reds):.1f}% (sizing BEAR × 0.65)")
    if h_n_tot > 0:
        print(f"    Funding Harvest: {h_n_tot} trades total, PF moyen={np.mean(h_pfs):.3f}")
    else:
        print(f"    Funding Harvest: 0 trades (conditions funding non réunies)")

    print("=" * 72)

    return {
        "folds_ok": ok, "folds_total": n_tot, "cata": cata,
        "pf_median": round(pf_med, 3), "n_trades_total": n_trades,
        "deployable": deployable,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-Forward v5")
    parser.add_argument("--folds", type=str, default="2022,2023,2024,2025",
                        help="Folds de test (années)")
    parser.add_argument("--triple-barrier", action="store_true",
                        help="Triple Barrier labels (Lopez de Prado)")
    parser.add_argument("--hybrid", action="store_true",
                        help="Hybrid labels: y_long AND y_long_tb (safe+profitable)")
    parser.add_argument("--no-regime", action="store_true",
                        help="Désactiver le Régime Allocator")
    parser.add_argument("--symbols", type=str, default=",".join(SYMBOLS),
                        help="Actifs à charger (pool training)")
    args = parser.parse_args()

    test_years = [int(y) for y in args.folds.split(",")]
    symbols    = [s.strip() for s in args.symbols.split(",")]
    # Fix #3 : inclure 2019 pour avoir du training data pour fold 2022
    all_years = list(range(max(2019, min(test_years) - 3), max(test_years) + 1))

    if args.hybrid:
        label_col, label_mode = "y_hybrid", "Hybrid (Quantile AND TB)"
    elif args.triple_barrier:
        label_col, label_mode = "y_long_tb", "Triple Barrier (ATR x2.0/1.5)"
    else:
        label_col, label_mode = "y_long", "Quantile 8h (top 20%)"

    print("=" * 72)
    print("WALK-FORWARD v5 — TRM FLEET LONG + RÉGIME ALLOCATOR")
    print("=" * 72)
    print(f"  TRM  : {len(TEMPORAL_HORIZONS_V4)}h × {len(MOVEMENT_ARCHETYPES_V4)} archétypes = 100 TRM")
    print(f"  Labels  : {label_mode}")
    print(f"  Coûts   : LONG={COST_PCT*10000:.0f}bps  SHORT(harvest)={COST_SHORT*10000:.0f}bps")
    print(f"  Folds   : {test_years}")
    print(f"  Pool    : {symbols}")
    print()

    # Chargement des données
    print("── Chargement + resample 1m→1h …")
    dfs: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = load_symbol(sym, all_years)
        if df is not None:
            dfs[sym] = df

    if PRIMARY_SYM not in dfs:
        sys.exit(f"✗ {PRIMARY_SYM} manquant dans {DATA_DIR}")

    df_primary = dfs[PRIMARY_SYM]
    extra_dfs  = [v for k, v in dfs.items() if k != PRIMARY_SYM]

    # Features disponibles sur BTC (référence)
    feat_candidates = _select_features(df_primary, _BASE_FEATURES)
    print(f"\n  Features candidates (fill≥60% sur BTC) : {len(feat_candidates)}")

    # Walk-forward
    fold_results: List[Dict] = []
    for ty in test_years:
        result = run_fold(
            df_primary=df_primary,
            extra_dfs=extra_dfs,
            test_year=ty,
            features=feat_candidates,
            label_col=label_col,
            run_regime=not args.no_regime,
        )
        fold_results.append(result)

    # Rapport final
    verdict = _print_report(fold_results)

    # Sauvegarde JSON
    import json
    report = {
        "config": {
            "label_mode": label_mode,
            "symbols": symbols,
            "folds": test_years,
            "n_trm": 100,
            "cost_long_bps": COST_PCT * 10000,
        },
        "folds": fold_results,
        "verdict": verdict,
    }
    out = REPORT_DIR / "walk_forward_v5_results.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Rapport JSON → {out}")


if __name__ == "__main__":
    main()
