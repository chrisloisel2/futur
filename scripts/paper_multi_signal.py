#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/paper_multi_signal.py — Paper Trading Multi-Asset Fleet (per-asset models)
====================================================================================

Un modèle TRM Fleet dédié par crypto du TOP 10.
Entraînement :
  - BTC  : primary=BTC,   extra=ETH (config validée DEPLOYABLE)
  - ETH  : primary=ETH,   extra=BTC
  - Autres : primary=asset, extra=BTC (régime)
Cache :
  - reports/paper_trading/.models/{SYMBOL}_{TRAIN_END}.pkl
  - Rechargé si déjà entraîné pour l'année courante
  - Flags : --retrain pour forcer, --train-only pour n'entraîner

Sorties :
  reports/paper_trading/{SYMBOL}/state.json
  reports/paper_trading/{SYMBOL}/signals.csv
  reports/paper_trading/{SYMBOL}/trades.csv
  reports/paper_trading/fleet_summary.json

Usage :
  python3 scripts/paper_multi_signal.py               # train si cache absent, puis signal
  python3 scripts/paper_multi_signal.py --retrain     # force ré-entraînement complet
  python3 scripts/paper_multi_signal.py --train-only  # entraîner sans générer signal
  python3 scripts/paper_multi_signal.py --dry-run     # signal sans logs
  python3 scripts/paper_multi_signal.py --symbols BTCUSDT SOLUSDT  # sous-ensemble
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_2.trm_fleet_long_v4 import (
    TRMFleetLongV4,
    calibrate_context_thresholds_v4,
    classify_context_v4,
)
from ai.level_0.labels import (
    compute_label_columns,
    build_labels,
    compute_long_regime_col,
)
from ai.level_0.features import get_available_features, FEATURES_LONG, FEATURES_COMMON
from ai.level_0.constants import COST_PCT, TARGET_COL
from ai.level_0.augmentation import augment_positives, regime_aware_augment
from ai.level_0.return_predictor import ReturnPredictor
from ai.meta.suppressor import MetaSuppressor
from risk.dynamic_sizing import DynamicSizer, apply_portfolio_correlation_cap
from config.deployment_status import LIVE_ENABLED, PAPER_ENABLED

try:
    from ai.level_2.exit_model_v1 import ExitFleetV1
    _EXIT_MODEL_AVAILABLE = True
except ImportError:
    ExitFleetV1 = None  # type: ignore
    _EXIT_MODEL_AVAILABLE = False

try:
    from ai.level_0.institutional_features import FEATURES_INST_LONG, FEATURES_INST_FILTER
except ImportError:
    FEATURES_INST_LONG = FEATURES_LONG
    FEATURES_INST_FILTER = FEATURES_COMMON

# ─── Configuration ────────────────────────────────────────────────────────────

ENRICHED_DIR  = ROOT / "data" / "enriched"
FLEET_DIR     = ROOT / "reports" / "paper_trading"
MODELS_DIR    = FLEET_DIR / ".models"
FLEET_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

FLEET_SUMMARY = FLEET_DIR / "fleet_summary.json"

CURRENT_YEAR         = datetime.now(timezone.utc).year
TRAIN_END            = CURRENT_YEAR - 1    # 2025 — fin des données d'entraînement
CAL_YEAR             = CURRENT_YEAR - 1    # 2025 — année de calibration (H2)
VAL_YEAR             = CURRENT_YEAR        # 2026 — validation forward (jan-mai 2026)
# Calibration = H2 CAL_YEAR (juillet–décembre 2025), exclu du training
CAL_MONTH_START      = 7                   # juillet

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]

# Pour chaque asset : quel est son "extra" de régime ?
# BTC → ETH (config DEPLOYABLE validée)
# Autres → BTC (contexte régime global)
EXTRA_MAP: Dict[str, str] = {
    "BTCUSDT": "ETHUSDT",
}
DEFAULT_EXTRA = "BTCUSDT"   # pour tous les non-BTC

PAPER_MIN_DURATION_DAYS = 90
PAPER_MIN_TRADES        = 100
PAPER_MIN_PF            = 1.30
PAPER_MAX_DD_PCT        = 3.0
PAPER_MIN_TRADES_FOR_PF = 30
SLIPPAGE_SIM_BPS        = int(COST_PCT * 10000)
HORIZON_HOURS           = 8    # durée max d'une position en heures
EXIT_MODEL_PATH         = MODELS_DIR / "exit_model_v1.pkl"


# ─── Data helpers ─────────────────────────────────────────────────────────────

def _asset_dir(symbol: str) -> Path:
    d = FLEET_DIR / symbol
    d.mkdir(parents=True, exist_ok=True)
    return d


def _model_path(symbol: str) -> Path:
    return MODELS_DIR / f"{symbol}_{TRAIN_END}.pkl"


def _ret_pred_path(symbol: str) -> Path:
    return MODELS_DIR / f"{symbol}_{TRAIN_END}_return_pred.pkl"


def _meta_path(symbol: str) -> Path:
    return MODELS_DIR / f"{symbol}_{TRAIN_END}_meta.json"


def _thr_path(symbol: str) -> Path:
    return MODELS_DIR / f"{symbol}_{TRAIN_END}_thresholds.json"


def _load_enriched(symbol: str, required_cols: Optional[List[str]] = None) -> Optional[pd.DataFrame]:
    path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        return None
    try:
        if required_cols is not None:
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(path)
            avail = set(pf.schema_arrow.names)
            _rv_sources = {"realized_volatility_14", "realized_volatility_20",
                           "realized_volatility_50", "realized_volatility_100"}
            _always = {
                "datetime", "open", "high", "low", "close", "Close", "volume",
                "dist_ema_50", "dist_ema_200", "dist_ema_20",
                "ema_spread_50_200", "rsi_14", "mom_logret_72", "mom_logret_168",
                "ema_spread_20_50", "rv_24",
            } | _rv_sources
            cols = list((set(required_cols) | _always) & avail)
            df = pd.read_parquet(path, columns=cols)
        else:
            df = pd.read_parquet(path)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        # rv_N aliases — computed from realized_volatility_N if not persisted yet
        _rv_map = {
            "rv_12": "realized_volatility_14",
            "rv_24": "realized_volatility_20",
            "rv_48": "realized_volatility_50",
            "rv_72": "realized_volatility_50",
            "rv_168": "realized_volatility_100",
        }
        for tgt, src in _rv_map.items():
            if tgt not in df.columns and src in df.columns:
                df[tgt] = df[src]
        if "rv_ratio_24_72" not in df.columns and "rv_24" in df.columns and "rv_72" in df.columns:
            df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].replace(0.0, float("nan"))
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"  WARN load {symbol}: {e}")
        return None


def _available_assets(targets: List[str]) -> Tuple[List[str], List[str]]:
    ok  = [s for s in targets if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    nok = [s for s in targets if s not in ok]
    return ok, nok


# ─── Position closing ─────────────────────────────────────────────────────────

def _record_pnl_to_state(state: dict, ret_net: float, size_mult: float, now: datetime) -> None:
    """Centralise la mise à jour du state après fermeture d'une position."""
    effective_ret = ret_net * 0.25 * size_mult
    outcome = "WIN" if ret_net > 0 else "LOSS"
    cum  = float(state.get("cumulative_pnl_pct", 0.0)) + effective_ret * 100
    state["cumulative_pnl_pct"] = round(cum, 4)
    peak = max(float(state.get("peak_pnl_pct", 0.0)), cum)
    state["peak_pnl_pct"] = round(peak, 4)
    dd   = max(0.0, peak - cum)
    state["max_dd_pct"] = round(max(float(state.get("max_dd_pct", 0.0)), dd), 4)
    if outcome == "WIN":
        state["total_wins"]         = state.get("total_wins", 0) + 1
        state["gross_win_sum"]      = float(state.get("gross_win_sum", 0.0)) + abs(ret_net)
        state["consecutive_losses"] = 0
    else:
        state["gross_loss_sum"]     = float(state.get("gross_loss_sum", 0.0)) + abs(ret_net)
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    state["last_trade_time"] = now.isoformat()
    state["weekly_pnl_pct"]  = round(
        float(state.get("weekly_pnl_pct", 0.0)) + effective_ret * 100, 4
    )


def _close_early_with_exit_model(
    symbol:        str,
    state:         dict,
    df_asset:      pd.DataFrame,
    exit_fleet:    "ExitFleetV1",
    current_price: float,
    dry_run:       bool = False,
) -> int:
    """
    Vérifie chaque position OPEN < HORIZON_HOURS contre le modèle de sortie.
    Si p_exit >= threshold (ou stop-loss dur -2.5%), ferme la position.
    Met aussi à jour les colonnes de suivi (max/min/prev_close) dans trades.csv.
    """
    trade_f = _asset_dir(symbol) / "trades.csv"
    if not trade_f.exists():
        return 0
    try:
        from ai.level_0.exit_labels import compute_position_state
        df_t = pd.read_csv(trade_f).fillna("")
        now  = datetime.now(timezone.utc)
        n_closed = 0
        changed  = False

        last_bar = df_asset.iloc[-1]
        rv_24    = float(last_bar.get("rv_24",
                    last_bar.get("realized_volatility_20", 0.02)))

        for i, row in df_t.iterrows():
            if str(row.get("outcome", "")).upper() != "OPEN":
                continue
            entry_time_str = str(row.get("entry_time", ""))
            if not entry_time_str:
                continue
            try:
                et = pd.Timestamp(entry_time_str)
                if et.tzinfo is None:
                    et = et.tz_localize("UTC")
                hours_held = (now - et.to_pydatetime()).total_seconds() / 3600.0
            except Exception:
                continue

            # L'expiration normale est gérée par _close_expired_positions
            if hours_held >= HORIZON_HOURS or hours_held < 1.0:
                continue

            entry_price = float(row.get("close_entry") or 0)
            if entry_price <= 0:
                continue

            bars_held = max(1, int(round(hours_held)))

            # Mise à jour du suivi max/min/prev
            max_c  = float(row.get("max_close_since_entry") or entry_price)
            min_c  = float(row.get("min_close_since_entry") or entry_price)
            prev_c = float(row.get("prev_close_for_exit")   or current_price)
            c3ago  = float(row.get("close_3ago_for_exit")   or current_price)

            max_c  = max(max_c,  current_price)
            min_c  = min(min_c,  current_price)

            # Features de position
            pos_state = compute_position_state(
                bars_held=bars_held,
                entry_price=entry_price,
                current_price=current_price,
                max_price=max_c,
                min_price=min_c,
                prev_price=prev_c,
                price_3ago=c3ago,
                rv_24=rv_24,
                entry_bar=dict(last_bar),
                max_hold=HORIZON_HOURS,
                cost_pct=COST_PCT,
            )

            # Inférence exit model
            should_exit, p_exit = exit_fleet.should_exit(last_bar, pos_state)

            # Hard SL -2.5% indépendant du modèle
            unrealized_ret = pos_state["unrealized_ret"]
            hard_sl        = unrealized_ret < -0.025

            # Écriture suivi dans tous les cas (maj colonnes tracking)
            df_t.at[i, "max_close_since_entry"] = round(max_c,  6)
            df_t.at[i, "min_close_since_entry"] = round(min_c,  6)
            df_t.at[i, "prev_close_for_exit"]   = round(current_price, 6)
            df_t.at[i, "close_3ago_for_exit"]   = round(prev_c, 6)
            df_t.at[i, "last_p_exit"]           = round(p_exit, 4)
            changed = True

            if not (should_exit or hard_sl) or dry_run:
                continue

            # Fermeture anticipée
            slip     = float(row.get("slippage_sim_bps") or SLIPPAGE_SIM_BPS) / 10_000
            ret_raw  = current_price / entry_price - 1.0
            ret_net  = ret_raw - slip * 2
            outcome  = "WIN" if ret_net > 0 else "LOSS"
            size_mult = float(row.get("size_multiplier") or 1.0)
            reason   = "EXIT_MODEL_SL" if hard_sl else "EXIT_MODEL"

            df_t.at[i, "exit_price"]      = round(current_price, 6)
            df_t.at[i, "exit_time"]       = now.isoformat()
            df_t.at[i, "hold_hours"]      = round(hours_held, 2)
            df_t.at[i, "future_ret_raw"]  = round(ret_raw * 100, 4)
            df_t.at[i, "future_ret_net"]  = round(ret_net * 100, 4)
            df_t.at[i, "pnl_pct"]         = round(ret_net * 100, 4)
            df_t.at[i, "outcome"]         = outcome
            df_t.at[i, "exit_reason"]     = reason
            df_t.at[i, "p_exit_at_close"] = round(p_exit, 4)

            _record_pnl_to_state(state, ret_net, size_mult, now)

            print(f"  [{symbol}] ◀ {reason}  "
                  f"p_exit={p_exit:.3f} thr={exit_fleet.threshold_:.2f}  "
                  f"ret={ret_net*100:+.2f}%  hold={hours_held:.1f}h  [{outcome}]")
            n_closed += 1

        if changed:
            df_t.to_csv(trade_f, index=False)
        if n_closed > 0:
            _save_state(symbol, state)
        return n_closed
    except Exception as e:
        print(f"  [{symbol}] WARN _close_early_with_exit_model: {e}")
        return 0


def _close_expired_positions(symbol: str, state: dict, current_price: float) -> int:
    """Ferme les positions OPEN qui ont dépassé HORIZON_HOURS. Retourne le nb fermé."""
    trade_f = _asset_dir(symbol) / "trades.csv"
    if not trade_f.exists():
        return 0
    try:
        df  = pd.read_csv(trade_f).fillna("")
        now = datetime.now(timezone.utc)
        n_closed = 0

        for i, row in df.iterrows():
            if str(row.get("outcome", "")).upper() != "OPEN":
                continue
            entry_time_str = str(row.get("entry_time", ""))
            if not entry_time_str:
                continue
            try:
                et = pd.Timestamp(entry_time_str)
                if et.tzinfo is None:
                    et = et.tz_localize("UTC")
                hours_held = (now - et.to_pydatetime()).total_seconds() / 3600.0
            except Exception:
                continue
            if hours_held < HORIZON_HOURS:
                continue

            entry_price = float(row.get("close_entry") or 0)
            if entry_price <= 0:
                continue

            slip      = float(row.get("slippage_sim_bps") or SLIPPAGE_SIM_BPS) / 10_000
            ret_raw   = current_price / entry_price - 1.0
            ret_net   = ret_raw - slip * 2          # slippage entrée + sortie
            outcome   = "WIN" if ret_net > 0 else "LOSS"
            size_mult = float(row.get("size_multiplier") or 1.0)
            # contribution au portefeuille : 25% de base × size_mult
            effective_ret = ret_net * 0.25 * size_mult

            df.at[i, "exit_price"]     = round(current_price, 6)
            df.at[i, "exit_time"]      = now.isoformat()
            df.at[i, "hold_hours"]     = round(hours_held, 2)
            df.at[i, "future_ret_raw"] = round(ret_raw * 100, 4)
            df.at[i, "future_ret_net"] = round(ret_net * 100, 4)
            df.at[i, "pnl_pct"]        = round(ret_net * 100, 4)
            df.at[i, "outcome"]        = outcome
            df.at[i, "exit_reason"]    = "HORIZON"

            _record_pnl_to_state(state, ret_net, size_mult, now)
            n_closed += 1

        if n_closed > 0:
            df.to_csv(trade_f, index=False)
            _save_state(symbol, state)
        return n_closed
    except Exception as e:
        print(f"  [{symbol}] WARN close_expired: {e}")
        return 0


# ─── Per-asset training ────────────────────────────────────────────────────────

def train_asset(symbol: str, force: bool = False) -> Tuple[Optional[TRMFleetLongV4], Dict, dict]:
    """
    Entraîne ou charge le TRM Fleet dédié à symbol.
    Retourne (fleet, thresholds, meta).
    """
    mp = _model_path(symbol)
    tp = _thr_path(symbol)
    mp_meta = _meta_path(symbol)

    rp = _ret_pred_path(symbol)

    if not force and mp.exists() and tp.exists():
        try:
            fleet      = joblib.load(mp)
            thresholds = json.loads(tp.read_text())
            meta       = json.loads(mp_meta.read_text()) if mp_meta.exists() else {}
            age_h      = (time.time() - mp.stat().st_mtime) / 3600
            # Charger le ReturnPredictor si disponible
            ret_pred   = joblib.load(rp) if rp.exists() else None
            meta["ret_pred"] = ret_pred
            print(f"  [{symbol}] modèle chargé depuis cache ({age_h:.0f}h)  "
                  f"n_val={meta.get('val_n','?')}  PF={meta.get('val_pf','?')}  "
                  f"WR={meta.get('val_wr','?')}"
                  + ("  [RetPred✓]" if ret_pred and ret_pred.fitted_ else ""))
            return fleet, thresholds, meta
        except Exception as e:
            print(f"  [{symbol}] cache corrompu ({e}) — ré-entraînement")

    # ── Chargement données ─────────────────────────────────────────────────────
    extra_sym = EXTRA_MAP.get(symbol, DEFAULT_EXTRA)
    df_primary_full = _load_enriched(symbol)
    if df_primary_full is None:
        print(f"  [{symbol}] ERREUR : enriched manquant")
        return None, {}, {}

    df_extra_full = _load_enriched(extra_sym)

    # ── Feature discovery sur le primary ─────────────────────────────────────
    cands_long = list(dict.fromkeys(FEATURES_INST_LONG + FEATURES_LONG))
    feat_long  = get_available_features(
        df_primary_full, cands_long, min_fill=0.75, context="LONG"
    )
    if len(feat_long) < 10:
        print(f"  [{symbol}] trop peu de features ({len(feat_long)}) — skip")
        return None, {}, {}

    required = list(set(feat_long))
    df_primary = _load_enriched(symbol,    required_cols=required)
    df_extra   = _load_enriched(extra_sym, required_cols=required)

    # ── Labels + régime ────────────────────────────────────────────────────────
    try:
        df_primary = compute_label_columns(df_primary)
        df_primary = compute_long_regime_col(df_primary)
    except Exception as e:
        print(f"  [{symbol}] labels error: {e}")
        return None, {}, {}

    years    = df_primary["datetime"].dt.year.values

    # ─── Split train / calibration / validation — SÉPARATION STRICTE ─────────
    # Cal     : H2 CAL_YEAR (juillet–décembre 2024)   ← exclu du train
    # Train   : ≤ TRAIN_END SAUF la période cal        ← pas de leakage
    # Val     : VAL_YEAR = 2025                        ← jamais touché
    dt_col   = pd.to_datetime(df_primary["datetime"], utc=True)
    cal_mask = (years == CAL_YEAR) & (dt_col.dt.month >= CAL_MONTH_START)
    tr_mask  = (years <= TRAIN_END) & ~cal_mask   # exclure cal du train
    val_mask = years == VAL_YEAR

    if tr_mask.sum() < 500:
        print(f"  [{symbol}] pas assez de données train ({tr_mask.sum()} barres) — skip")
        return None, {}, {}
    if cal_mask.sum() < 50:
        print(f"  [{symbol}] pas assez de données cal ({cal_mask.sum()} barres) — skip")
        return None, {}, {}

    df_primary, _ = build_labels(df_primary, tr_mask)

    # ── Pool multi-actif : primary + extra ────────────────────────────────────
    df_primary_train = df_primary.loc[tr_mask].copy()
    dfs_train = [df_primary_train]

    if df_extra is not None and extra_sym != symbol:
        try:
            df_extra_proc = compute_label_columns(df_extra.copy())
            df_extra_proc = compute_long_regime_col(df_extra_proc)
            yrs_ex = df_extra_proc["datetime"].dt.year.values
            tr_ex  = yrs_ex <= TRAIN_END
            if tr_ex.sum() >= 500:
                df_ex_lab, _ = build_labels(df_extra_proc, tr_ex)
                n_present = sum(1 for f in feat_long if f in df_extra_proc.columns)
                if n_present / max(len(feat_long), 1) >= 0.65:
                    dfs_train.append(df_ex_lab.loc[tr_ex].copy())
        except Exception:
            pass

    df_train = pd.concat(dfs_train, ignore_index=True)

    # ── Oversampling par régime (v3) ──────────────────────────────────────────
    n_pos = int((df_train.get("y_long", pd.Series(dtype=int)) == 1).sum()) \
            if "y_long" in df_train.columns else 0
    if 30 <= n_pos < 4000:
        try:
            _fs = [f for f in feat_long if f in df_train.columns]
            if "regime_long" in df_train.columns:
                # v3 : oversampling différencié par régime de marché
                df_train = regime_aware_augment(
                    df_train, features=_fs, label_col="y_long",
                    regime_col="regime_long", global_target_pos=3000,
                )
            else:
                # Fallback SMOTE classique si pas de colonne régime
                mult = min(3, max(1, 2000 // max(n_pos, 1)))
                df_train = augment_positives(
                    df_train, features=_fs, label_col="y_long", multiplier=mult
                )
        except Exception as e:
            pass

    # Feature availability sur le primary (pas de dilution altcoin)
    feat_avail = [
        f for f in feat_long
        if f in df_primary_train.columns and df_primary_train[f].notna().mean() >= 0.75
    ]

    # ── Jeux calibration et validation ───────────────────────────────────────
    df_cal = df_primary.loc[cal_mask].copy()
    df_val = df_primary.loc[val_mask].copy() if val_mask.sum() >= 50 else pd.DataFrame()

    # ── Entraîner fleet ───────────────────────────────────────────────────────
    t0    = time.time()
    fleet = TRMFleetLongV4(features=feat_avail)
    fleet.train(
        df_train, np.ones(len(df_train), dtype=bool),
        df_val_btc=df_cal,
        val_mask_in_btc=np.ones(len(df_cal), dtype=bool),
        label_col="y_long",
    )

    # ── Calibration seuils SUR 2024 (jamais sur 2025) ────────────────────────
    ret_cal    = df_cal[TARGET_COL].fillna(0.0).values if TARGET_COL in df_cal.columns \
                 else np.zeros(len(df_cal))
    thresholds = calibrate_context_thresholds_v4(
        fleet, df_cal,
        filter_p=np.ones(len(df_cal)), filter_thr=0.50,
        ret_cal=ret_cal, cost_pct=COST_PCT,
    )
    adapt = fleet.adaptive_threshold()
    thresholds = {k: max(v, adapt) for k, v in thresholds.items()}

    # ── Floor adaptatif par asset basé sur la qualité du signal calibration ──
    # Si la qualité cal est faible → relever le floor pour éviter l'over-trading
    n_cal   = len(df_cal)
    preds_c = fleet.predict(df_cal, np.ones(n_cal, dtype=bool))
    ctx_c   = classify_context_v4(df_cal)
    sigs_c  = np.zeros(n_cal, dtype=int)
    for i in range(n_cal):
        thr_c = thresholds.get(str(ctx_c[i]), thresholds.get("general", adapt))
        if preds_c[i] >= thr_c:
            sigs_c[i] = 1
    if "regime_long" in df_cal.columns:
        sigs_c[df_cal["regime_long"].values == "NO_LONG"] = 0
    idx_c = np.where(sigs_c == 1)[0]
    fut_c  = ret_cal
    gw_c   = sum(max(fut_c[i] - COST_PCT, 0) for i in idx_c)
    gl_c   = sum(max(COST_PCT - fut_c[i], 0) for i in idx_c)
    cal_pf = gw_c / max(gl_c, 1e-9)
    cal_n  = len(idx_c)

    # Relever le floor si PF calibration insuffisant
    if cal_pf < 1.0:
        floor = min(adapt + 0.08, 0.70)   # PF < 1 : signal fortement dégradé
    elif cal_pf < 1.5:
        floor = min(adapt + 0.04, 0.66)   # PF faible : être plus sélectif
    else:
        floor = adapt                       # PF correct : garder la calibration
    thresholds = {k: max(v, floor) for k, v in thresholds.items()}

    print(f"  [{symbol}] Cal {CAL_YEAR}: n={cal_n}  PF={cal_pf:.2f}  floor={floor:.2f}")

    # ── Stats de validation VRAIE FORWARD (2025) — jamais utilisée pour calibrer ─
    if len(df_val) > 0:
        n_val   = len(df_val)
        preds_v = fleet.predict(df_val, np.ones(n_val, dtype=bool))
        ctx_v   = classify_context_v4(df_val)
        sigs_v  = np.zeros(n_val, dtype=int)
        for i in range(n_val):
            thr_v = thresholds.get(str(ctx_v[i]), thresholds.get("general", floor))
            if preds_v[i] >= thr_v:
                sigs_v[i] = 1
        if "regime_long" in df_val.columns:
            sigs_v[df_val["regime_long"].values == "NO_LONG"] = 0
        idx_v  = np.where(sigs_v == 1)[0]
        fut_v  = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns \
                 else np.zeros(n_val)
        gw_v   = sum(max(fut_v[i] - COST_PCT, 0) for i in idx_v)
        gl_v   = sum(max(COST_PCT - fut_v[i], 0) for i in idx_v)
        pf     = round(gw_v / max(gl_v, 1e-9), 3)
        wr     = round(sum(1 for i in idx_v if fut_v[i] > COST_PCT) / max(len(idx_v), 1), 3)
        val_n  = len(idx_v)
    else:
        pf, wr, val_n = 0.0, 0.0, 0

    elapsed = time.time() - t0

    meta = {
        "symbol":       symbol,
        "extra_sym":    extra_sym,
        "train_end":    TRAIN_END,
        "cal_year":     CAL_YEAR,
        "val_year":     VAL_YEAR,
        "n_features":   len(feat_avail),
        "n_train":      int(tr_mask.sum()),
        "n_cal":        int(cal_mask.sum()),
        "n_val":        int(val_mask.sum()),
        "cal_pf":       round(min(cal_pf, 999.0), 3),
        "cal_n":        cal_n,
        "val_n":        val_n,
        "val_pf":       round(min(pf, 999.0), 3),   # vrai forward (2025)
        "val_wr":       wr,
        "val_wr_pct":   f"{wr*100:.0f}%",
        "threshold_floor": round(floor, 2),
        "elapsed_s":    round(elapsed, 1),
        "trained_at":   datetime.now(timezone.utc).isoformat(),
    }

    # ── ReturnPredictor (v3 : multi-task régression) ─────────────────────────
    ret_pred = ReturnPredictor()
    try:
        ret_pred.fit(df_primary, feat_avail, tr_mask)
        joblib.dump(ret_pred, rp)
    except Exception as e:
        print(f"  [{symbol}] WARN ReturnPredictor: {e}")
        ret_pred = None

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    joblib.dump(fleet, mp)
    tp.write_text(json.dumps(thresholds))
    meta["ret_pred"] = ret_pred   # en mémoire seulement (pas dans JSON)
    _meta_path(symbol).write_text(
        json.dumps({k: v for k, v in meta.items() if k != "ret_pred"}, indent=2)
    )

    print(f"  [{symbol}] ✓ train {elapsed:.0f}s  "
          f"fwd {VAL_YEAR}: n={val_n}  PF={pf:.3f}  WR={wr:.0%}  "
          f"feat={len(feat_avail)}  → {mp.name}")
    return fleet, thresholds, meta


# ─── State per asset ──────────────────────────────────────────────────────────

def _load_state(symbol: str) -> dict:
    f = _asset_dir(symbol) / "state.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {
        "symbol": symbol, "start_date": None,
        "total_signals": 0, "total_trades": 0, "total_wins": 0, "total_losses": 0,
        "gross_win_sum": 0.0, "gross_loss_sum": 0.0,
        "cumulative_pnl_pct": 0.0, "peak_pnl_pct": 0.0, "max_dd_pct": 0.0,
        "consecutive_losses": 0, "last_trade_time": None,
        "weekly_pnl_pct": 0.0, "weekly_start_time": None, "crash_halt_until": None,
        "drift_alerts": 0, "accounting_errors": 0,
    }


def _save_state(symbol: str, state: dict) -> None:
    (_asset_dir(symbol) / "state.json").write_text(json.dumps(state, indent=2, default=str))


def _append_csv(symbol: str, name: str, row: dict) -> None:
    f  = _asset_dir(symbol) / name
    df = pd.read_csv(f) if f.exists() else pd.DataFrame()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(f, index=False)


# ─── Guardrails ───────────────────────────────────────────────────────────────

def _check_guards(symbol: str, state: dict, df_btc: pd.DataFrame) -> Tuple[bool, str]:
    now = datetime.now(timezone.utc)

    # Crash BTC global
    if state.get("crash_halt_until"):
        halt = pd.Timestamp(state["crash_halt_until"])
        if halt.tzinfo is None:
            halt = halt.tz_localize("UTC")
        if now < halt.to_pydatetime():
            return True, f"CRASH_HALT_BTC jusqu'au {halt.date()}"

    close_btc = df_btc["close"].values
    if len(close_btc) >= 1440:
        ret_60d = float(close_btc[-1] / close_btc[-1440] - 1.0)
        if ret_60d < -0.30:
            state["crash_halt_until"] = (now + timedelta(days=30)).isoformat()
            _save_state(symbol, state)
            return True, f"CRASH_BTC {ret_60d*100:.1f}%/60j"

    # Pertes consécutives
    if state["consecutive_losses"] > 4 and state.get("last_trade_time"):
        lt = pd.Timestamp(state["last_trade_time"])
        if lt.tzinfo is None:
            lt = lt.tz_localize("UTC")
        if now < (lt + pd.Timedelta(hours=48)).to_pydatetime():
            return True, f"CONSEC_LOSSES={state['consecutive_losses']}"
        state["consecutive_losses"] = 0
        _save_state(symbol, state)

    # Weekly DD
    ws = state.get("weekly_start_time")
    if ws:
        wst = pd.Timestamp(ws)
        if wst.tzinfo is None:
            wst = wst.tz_localize("UTC")
        if (now - wst.to_pydatetime()).days >= 7:
            state["weekly_pnl_pct"]    = 0.0
            state["weekly_start_time"] = now.isoformat()
            _save_state(symbol, state)
    else:
        state["weekly_start_time"] = now.isoformat()
        _save_state(symbol, state)

    if state.get("weekly_pnl_pct", 0.0) <= -5.0:
        return True, f"WEEKLY_LOSS {state['weekly_pnl_pct']:.1f}%"

    return False, ""


# ─── Signal par asset ─────────────────────────────────────────────────────────

def run_asset_signal(
    symbol:          str,
    df_asset:        pd.DataFrame,
    df_btc:          pd.DataFrame,
    fleet:           TRMFleetLongV4,
    thresholds:      Dict[str, float],
    btc_vs_ema200:   float,
    btc_regime:      str,
    state:           dict,
    dry_run:         bool = False,
    exit_fleet:      Optional[object] = None,
    open_positions:  Optional[dict] = None,
    fleet_meta:      Optional[dict] = None,   # contient ret_pred (v3)
) -> dict:
    now = datetime.now(timezone.utc)

    # ── Fermeture (exit model anticipé + horizon 8h) ───────────────────────────
    if not dry_run:
        last_bar  = df_asset.iloc[-1]
        close_now = float(last_bar.get("close", last_bar.get("Close", 0.0)))

        # 1. Exit model : décision anticipée sur positions < 8h
        if exit_fleet is not None:
            n_early = _close_early_with_exit_model(
                symbol, state, df_asset, exit_fleet, close_now
            )
            if n_early:
                print(f"  [{symbol}] {n_early} sortie(s) anticipée(s) via exit model")

        # 2. Horizon : fermeture des positions expirées ≥ 8h
        n_closed = _close_expired_positions(symbol, state, close_now)
        if n_closed:
            print(f"  [{symbol}] {n_closed} position(s) fermée(s) à {close_now:.4f}")

    if state["start_date"] is None and not dry_run:
        state["start_date"] = now.isoformat()
        _save_state(symbol, state)

    blocked, block_reason = _check_guards(symbol, state, df_btc)

    try:
        df_live = compute_label_columns(df_asset.copy())
        df_live = compute_long_regime_col(df_live)
    except Exception:
        df_live = df_asset.copy()

    last_bar  = df_live.iloc[-1]
    close_val = float(last_bar.get("close", last_bar.get("Close", 0.0)))

    if blocked:
        row = {"symbol": symbol, "timestamp": now.isoformat(),
               "action": f"BLOCKED", "block_reason": block_reason,
               "p_long": None, "threshold": None,
               "btc_regime": btc_regime, "close": round(close_val, 6), "blocked": True}
        state["total_signals"] += 1
        if not dry_run:
            _append_csv(symbol, "signals.csv", row)
            _save_state(symbol, state)
        return row

    ones   = np.array([True])
    p_last = float(fleet.predict(df_live.iloc[[-1]], ones)[0])
    ctx    = str(classify_context_v4(df_live.iloc[[-1]])[0])
    thr    = thresholds.get(ctx, thresholds.get("general", 0.55))
    regime_last = str(last_bar.get("regime_long", "NEUTRAL"))

    suppressor = MetaSuppressor()
    regime_str = "BEAR" if btc_vs_ema200 < -0.02 else ("EXPANSION" if btc_vs_ema200 > 0.05 else "RECOVERY")
    sup = suppressor.evaluate(
        bar=last_bar,
        predictions={"p_long": p_last},
        regime=regime_str,
        side="long",
    )

    sizer    = DynamicSizer(target_annual_vol=0.15)
    rv24     = float(last_bar.get("rv_24", 0.02))
    reg_mult = 0.65 if btc_regime == "BEAR" else 1.0
    sizing   = sizer.compute_size(
        base_size=1.0, vol_24h=rv24,
        regime_mult=reg_mult,
        liquidity_mult=sup.size_multiplier,
    )

    # ── ReturnPredictor : GATE ONLY v3.1 ─────────────────────────────────────
    # ret_boost supprimé (R²=0.02 → sizing aléatoire amplifiait les losers)
    # Nouveau rôle : pred_ret_z < 0 → bloquer le trade (filtre EV négatif)
    ret_pred   = fleet_meta.get("ret_pred") if fleet_meta else None
    pred_ret_z = 0.0
    gate_blocked_by_ret_pred = False
    if ret_pred is not None and ret_pred.fitted_:
        try:
            pred_ret_z = ret_pred.single_zscore(last_bar, rv_24=rv24)
            if pred_ret_z < 0.0:
                gate_blocked_by_ret_pred = True
        except Exception:
            pred_ret_z = 0.0

    size_before_gate = sizing.final_size   # pour log

    # ── Correlation cap portfolio (v2) ────────────────────────────────────────
    corr_size = size_before_gate           # sizing inchangé — plus de ret_boost
    if open_positions:
        corr_size = apply_portfolio_correlation_cap(
            open_positions=open_positions,
            new_symbol=symbol,
            new_size=sizing.final_size,
            max_portfolio_vol=0.20,
        )

    no_long   = regime_last == "NO_LONG"
    sup_block = not sup.allow
    raw       = p_last >= thr

    if no_long:
        action = "NO_SIGNAL"
    elif sup_block:
        action = "NO_SIGNAL"
    elif gate_blocked_by_ret_pred:        # v3.1 : gate ReturnPredictor
        action = "NO_SIGNAL"
    elif raw:
        action = "LONG"
    elif p_last >= thr * 0.90:
        action = "WATCH"
    else:
        action = "NO_SIGNAL"

    row = {
        "symbol": symbol, "timestamp": now.isoformat(),
        "action": action, "p_long": round(p_last, 4), "threshold": round(thr, 4),
        "context": ctx, "regime_long": regime_last, "btc_regime": btc_regime,
        "sup_level": sup.level, "sup_score": round(sup.score, 4),
        "size_mult": round(corr_size, 4),
        "close": round(close_val, 6), "blocked": False,
        # ── logs v3.1 ─────────────────────────────────────────────────────────
        "pred_ret_z":               round(pred_ret_z, 3),
        "ret_boost":                1.0,
        "size_before_ret_boost":    round(size_before_gate, 4),
        "size_final":               round(corr_size, 4),
        "gate_blocked_by_ret_pred": gate_blocked_by_ret_pred,
        # ── snapshot features E (monitoring SOL) ─────────────────────────────
        "cvd_4h_z":          round(float(last_bar.get("cvd_4h_z", float("nan"))), 4),
        "cvd_24h_z":         round(float(last_bar.get("cvd_24h_z", float("nan"))), 4),
        "cvd_momentum":      round(float(last_bar.get("cvd_momentum", float("nan"))), 4),
        "basis_annualized":  round(float(last_bar.get("basis_annualized", float("nan"))), 2),
    }

    state["total_signals"] += 1

    if action == "LONG" and not dry_run:
        if corr_size < 0.01:
            # Corrélation portfolio trop élevée — on bloque l'entrée
            action = "NO_SIGNAL"
            row["action"] = action
            row["block_reason"] = "CORRELATION_CAP"
        else:
            trade = {
                "entry_time": now.isoformat(), "symbol": symbol, "context": ctx,
                "p_long": round(p_last, 4), "threshold": round(thr, 4),
                "close_entry": round(close_val, 6), "regime_long": regime_last,
                "suppressor_level": sup.level, "suppressor_score": round(sup.score, 4),
                "size_multiplier": round(corr_size, 4),
                "vol_24h": round(rv24, 5), "regime_mult": round(reg_mult, 2),
                "future_ret_raw": None, "future_ret_net": None,
                "outcome": "OPEN", "slippage_sim_bps": SLIPPAGE_SIM_BPS,
            }
            _append_csv(symbol, "trades.csv", trade)
            state["total_trades"] += 1

    if not dry_run:
        _append_csv(symbol, "signals.csv", row)
        _save_state(symbol, state)

    return row


# ─── Gates ───────────────────────────────────────────────────────────────────

def _gates(state: dict) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = state.get("start_date")
    dur_days = 0
    if start:
        try:
            st = pd.Timestamp(start)
            if st.tzinfo is None: st = st.tz_localize("UTC")
            dur_days = max(0, (now - st.to_pydatetime()).days)
        except Exception:
            pass
    n_trades = state.get("total_trades", 0)
    gw       = state.get("gross_win_sum", 0.0)
    gl       = state.get("gross_loss_sum", 0.0)
    dd       = state.get("max_dd_pct", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= PAPER_MIN_TRADES_FOR_PF else None
    return [
        {"name": "Durée ≥ 90j",    "ok": dur_days >= 90,  "value": dur_days,     "target": 90,   "unit": "j"},
        {"name": "Trades ≥ 100",   "ok": n_trades >= 100, "value": n_trades,     "target": 100,  "unit": ""},
        {"name": "PF live ≥ 1.30", "ok": pf is not None and pf >= 1.30,
         "value": pf,              "target": 1.30, "unit": ""},
        {"name": "DD < 3%",        "ok": dd < 3.0,        "value": round(dd, 2), "target": 3.0,  "unit": "%"},
        {"name": "Erreurs",        "ok": state.get("accounting_errors", 0) == 0,
         "value": state.get("accounting_errors", 0), "target": 0, "unit": ""},
        {"name": "Drift",          "ok": state.get("drift_alerts", 0) == 0,
         "value": state.get("drift_alerts", 0),      "target": 0, "unit": ""},
    ]


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrain",    action="store_true", help="Forcer ré-entraînement")
    parser.add_argument("--train-only", action="store_true", help="Entraîner sans signal")
    parser.add_argument("--dry-run",    action="store_true", help="Signal sans logs")
    parser.add_argument("--reset-state",action="store_true", help="Reset états paper")
    parser.add_argument("--symbols",    nargs="+", default=None)
    args = parser.parse_args()

    t0  = time.time()
    now = datetime.now(timezone.utc)

    print("=" * 68)
    print("  PAPER MULTI-ASSET — TRM Fleet v4 — modèle dédié par asset")
    print(f"  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Train ≤{TRAIN_END}  Val {VAL_YEAR}  cache: {MODELS_DIR.relative_to(ROOT)}")
    print("=" * 68)

    target    = args.symbols if args.symbols else TOP_10
    available, missing = _available_assets(target)

    if missing:
        print(f"\n  Assets sans parquet (bootstrap requis) :")
        print(f"  python3 scripts/bootstrap_enriched.py --symbols {' '.join(missing)}")

    if not available:
        print("  Aucun asset disponible. Abandon.")
        sys.exit(1)

    print(f"\n  Assets : {', '.join(available)}  ({len(available)}/{len(target)})")

    if args.reset_state:
        for sym in available:
            sf = _asset_dir(sym) / "state.json"
            if sf.exists():
                sf.unlink()
        print("  [RESET] States effacés.")

    # ── BTC régime (global) ───────────────────────────────────────────────────
    df_btc_regime = _load_enriched("BTCUSDT")
    if df_btc_regime is None:
        print("  ERREUR : BTCUSDT enriched manquant pour le régime global.")
        sys.exit(1)
    close_btc  = df_btc_regime["close"].values
    ema200_btc = float(pd.Series(close_btc).ewm(span=200, adjust=False).mean().iloc[-1])
    btc_vs_ema = (close_btc[-1] / ema200_btc - 1.0)
    btc_regime = "BULL" if btc_vs_ema > 0 else "BEAR"
    btc_price  = round(float(close_btc[-1]), 2)
    ret_7d     = (close_btc[-1] / close_btc[-min(168, len(close_btc)-1)] - 1.0) * 100

    print(f"\n  BTC: {btc_price:,.0f} USDT  [{btc_regime}]  "
          f"EMA200={btc_vs_ema*100:+.1f}%  7j={ret_7d:+.1f}%")

    # ── Chargement du modèle de sortie (optionnel) ───────────────────────────
    exit_fleet = None
    if _EXIT_MODEL_AVAILABLE and EXIT_MODEL_PATH.exists():
        try:
            exit_fleet = joblib.load(EXIT_MODEL_PATH)
            print(f"  [ExitModel] Chargé  thr={exit_fleet.threshold_:.2f}  "
                  f"specs={len(exit_fleet.specialists)}  feats={len(exit_fleet.features_)}")
        except Exception as e:
            print(f"  [ExitModel] WARN chargement : {e}")
    else:
        if _EXIT_MODEL_AVAILABLE:
            print(f"  [ExitModel] Absent — entraîner avec : python scripts/train_exit_model.py")

    # ── Phase 1 : Entraînement ────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  PHASE 1 — Entraînement ({len(available)} modèles)")
    print(f"{'─'*68}")

    models: Dict[str, Tuple[TRMFleetLongV4, Dict, dict]] = {}

    for sym in available:
        t_sym = time.time()
        fleet, thresholds, meta = train_asset(sym, force=args.retrain)
        if fleet is None:
            continue
        models[sym] = (fleet, thresholds, meta)

    print(f"\n  {len(models)}/{len(available)} modèles prêts  "
          f"({time.time()-t0:.0f}s écoulé)")

    if args.train_only:
        print("\n  [--train-only] terminé.")
        return

    # ── Phase 2 : Inférence ───────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  PHASE 2 — Inférence ({len(models)} assets)")
    print(f"{'─'*68}")

    fleet_results = []

    # Construire le dict des positions ouvertes pour le correlation cap
    def _build_open_positions() -> dict:
        """Retourne {symbol: {size, rv_24}} pour toutes les positions OPEN."""
        positions: dict = {}
        for s in available:
            trade_f = _asset_dir(s) / "trades.csv"
            if not trade_f.exists():
                continue
            try:
                df_t = pd.read_csv(trade_f).fillna("")
                for _, row in df_t.iterrows():
                    if str(row.get("outcome", "")).upper() != "OPEN":
                        continue
                    positions[s] = {
                        "size":  float(row.get("size_multiplier", 1.0)) * 0.25,
                        "rv_24": float(row.get("vol_24h", 0.02)),
                    }
                    break  # une seule position par asset
            except Exception:
                pass
        return positions

    for sym in available:
        if sym not in models:
            continue
        fleet, thresholds, meta = models[sym]

        df_asset = _load_enriched(sym, required_cols=fleet.features)
        if df_asset is None:
            continue

        state = _load_state(sym)
        t_sym = time.time()

        try:
            sig = run_asset_signal(
                sym, df_asset, df_btc_regime, fleet, thresholds,
                btc_vs_ema, btc_regime, state,
                dry_run=args.dry_run,
                exit_fleet=exit_fleet,
                open_positions=_build_open_positions(),
                fleet_meta=meta,
            )
        except Exception as e:
            import traceback
            print(f"   [{sym}] ERREUR signal : {e}")
            traceback.print_exc()
            sig = {"symbol": sym, "timestamp": now.isoformat(), "action": "ERROR",
                   "p_long": None, "threshold": None, "btc_regime": btc_regime,
                   "close": None, "error": str(e)}

        # Enrichir avec métriques paper
        state_fresh = _load_state(sym)
        g = _gates(state_fresh)
        g_ok = all(x["ok"] for x in g)
        n_t  = state_fresh.get("total_trades", 0)
        gw   = state_fresh.get("gross_win_sum", 0.0)
        gl   = state_fresh.get("gross_loss_sum", 0.0)
        pf   = round(gw / max(gl, 1e-9), 3) if n_t >= PAPER_MIN_TRADES_FOR_PF else None
        wr   = round(state_fresh.get("total_wins", 0) / max(n_t, 1), 3) if n_t > 0 else None

        sig.update({
            "total_trades":       n_t,
            "pf_live":            pf,
            "wr_live":            wr,
            "max_dd_pct":         state_fresh.get("max_dd_pct", 0.0),
            "cumulative_pnl_pct": state_fresh.get("cumulative_pnl_pct", 0.0),
            "all_gates_ok":       g_ok,
            "verdict":            "LIVE_CANDIDATE" if g_ok else "PAPER_ONLY",
            "start_date":         state_fresh.get("start_date"),
            "val_pf":             min(meta.get("val_pf") or 0, 999.0) if meta.get("val_pf") is not None else None,
            "val_wr":             meta.get("val_wr"),
            "val_n":              meta.get("val_n"),
            "n_features":         meta.get("n_features"),
        })
        fleet_results.append(sig)

        action_d = sig.get("action", "?")
        p_d = f"p={sig['p_long']:.4f}" if sig.get("p_long") is not None else ""
        dt_ms = (time.time() - t_sym) * 1000
        print(f"   {sym:<12} {action_d:<22} {p_d:<14} "
              f"val_PF={meta.get('val_pf','?'):<8} [{dt_ms:.0f}ms]")

    # ── Fleet summary ─────────────────────────────────────────────────────────
    summary = {
        "timestamp":      now.isoformat(),
        "btc_regime":     btc_regime,
        "btc_price":      btc_price,
        "btc_vs_ema200":  round(btc_vs_ema * 100, 2),
        "assets":         fleet_results,
        "n_assets":       len(fleet_results),
        "n_long_signals": sum(1 for r in fleet_results if r.get("action") == "LONG"),
        "n_watch":        sum(1 for r in fleet_results if r.get("action") == "WATCH"),
        "elapsed_s":      round(time.time() - t0, 1),
        "model_type":     "per_asset_dedicated",
    }
    FLEET_SUMMARY.write_text(json.dumps(summary, indent=2, default=str))

    # ── Récapitulatif ─────────────────────────────────────────────────────────
    print(f"\n{'═'*68}")
    print(f"  {'ASSET':<10} {'ACTION':<18} {'p_long':>7} {'thr':>6} "
          f"{'val_PF':>7} {'val_WR':>7} {'val_n':>6}")
    print(f"  {'─'*10} {'─'*18} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*6}")
    for r in fleet_results:
        sym = r["symbol"].replace("USDT","")
        act = r.get("action","?")
        p   = f"{r['p_long']:.4f}" if r.get("p_long") is not None else "  –"
        thr = f"{r['threshold']:.4f}" if r.get("threshold") is not None else "  –"
        raw_vp = r.get("val_pf")
        vp = f"{min(raw_vp, 999.0):.2f}" if raw_vp is not None else " –"
        vw  = f"{r['val_wr']*100:.0f}%" if r.get("val_wr") is not None else "  –"
        vn  = str(r.get("val_n","–"))
        mark = " ◀ LONG" if act == "LONG" else (" ○ watch" if act == "WATCH" else "")
        print(f"  {sym:<10} {act:<18} {p:>7} {thr:>6} {vp:>7} {vw:>7} {vn:>6}{mark}")

    n_long = summary["n_long_signals"]
    n_watch= summary["n_watch"]
    print(f"\n  LONG: {n_long}  WATCH: {n_watch}  [{btc_regime}]  "
          f"total: {time.time()-t0:.0f}s")
    print(f"{'═'*68}")
    print("  !! PAPER TRADING UNIQUEMENT — LIVE_ENABLED=False !!")
    print(f"{'═'*68}")


if __name__ == "__main__":
    main()
