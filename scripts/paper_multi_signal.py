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
from ai.level_0.augmentation import augment_positives
from ai.meta.suppressor import MetaSuppressor
from risk.dynamic_sizing import DynamicSizer
from config.deployment_status import LIVE_ENABLED, PAPER_ENABLED

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

CURRENT_YEAR = datetime.now(timezone.utc).year
TRAIN_END    = CURRENT_YEAR - 2
VAL_YEAR     = CURRENT_YEAR - 1

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


# ─── Data helpers ─────────────────────────────────────────────────────────────

def _asset_dir(symbol: str) -> Path:
    d = FLEET_DIR / symbol
    d.mkdir(parents=True, exist_ok=True)
    return d


def _model_path(symbol: str) -> Path:
    return MODELS_DIR / f"{symbol}_{TRAIN_END}.pkl"


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
            _always = {
                "datetime", "open", "high", "low", "close", "Close", "volume",
                "dist_ema_50", "dist_ema_200", "dist_ema_20",
                "ema_spread_50_200", "rsi_14", "mom_logret_72", "mom_logret_168",
                "ema_spread_20_50", "rv_24",
            }
            cols = list((set(required_cols) | _always) & avail)
            df = pd.read_parquet(path, columns=cols)
        else:
            df = pd.read_parquet(path)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"  WARN load {symbol}: {e}")
        return None


def _available_assets(targets: List[str]) -> Tuple[List[str], List[str]]:
    ok  = [s for s in targets if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    nok = [s for s in targets if s not in ok]
    return ok, nok


# ─── Per-asset training ────────────────────────────────────────────────────────

def train_asset(symbol: str, force: bool = False) -> Tuple[Optional[TRMFleetLongV4], Dict, dict]:
    """
    Entraîne ou charge le TRM Fleet dédié à symbol.
    Retourne (fleet, thresholds, meta).
    """
    mp = _model_path(symbol)
    tp = _thr_path(symbol)
    mp_meta = _meta_path(symbol)

    if not force and mp.exists() and tp.exists():
        try:
            fleet      = joblib.load(mp)
            thresholds = json.loads(tp.read_text())
            meta       = json.loads(mp_meta.read_text()) if mp_meta.exists() else {}
            age_h      = (time.time() - mp.stat().st_mtime) / 3600
            print(f"  [{symbol}] modèle chargé depuis cache ({age_h:.0f}h)  "
                  f"n_val={meta.get('val_n','?')}  PF={meta.get('val_pf','?')}  "
                  f"WR={meta.get('val_wr','?')}")
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
    tr_mask  = years <= TRAIN_END
    val_mask = years == VAL_YEAR

    if tr_mask.sum() < 500:
        print(f"  [{symbol}] pas assez de données train ({tr_mask.sum()} barres) — skip")
        return None, {}, {}
    if val_mask.sum() < 50:
        print(f"  [{symbol}] pas assez de données val ({val_mask.sum()} barres) — skip")
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

    # ── SMOTE ─────────────────────────────────────────────────────────────────
    n_pos = int((df_train.get("y_long", pd.Series(dtype=int)) == 1).sum()) \
            if "y_long" in df_train.columns else 0
    if 50 <= n_pos < 2000:
        try:
            _fs   = [f for f in feat_long if f in df_train.columns]
            mult  = min(3, max(1, 2000 // max(n_pos, 1)))
            df_train = augment_positives(df_train, features=_fs, label_col="y_long", multiplier=mult)
        except Exception:
            pass

    # Feature availability sur le primary (pas de dilution altcoin)
    feat_avail = [
        f for f in feat_long
        if f in df_primary_train.columns and df_primary_train[f].notna().mean() >= 0.75
    ]

    # ── Validation sur le primary ──────────────────────────────────────────────
    df_val = df_primary.loc[val_mask].copy()

    # ── Entraîner fleet ───────────────────────────────────────────────────────
    t0    = time.time()
    fleet = TRMFleetLongV4(features=feat_avail)
    fleet.train(
        df_train, np.ones(len(df_train), dtype=bool),
        df_val_btc=df_val,
        val_mask_in_btc=np.ones(len(df_val), dtype=bool),
        label_col="y_long",
    )

    # ── Calibration seuils ────────────────────────────────────────────────────
    ret_val    = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns \
                 else np.zeros(len(df_val))
    thresholds = calibrate_context_thresholds_v4(
        fleet, df_val,
        filter_p=np.ones(len(df_val)), filter_thr=0.50,
        ret_val=ret_val, cost_pct=COST_PCT,
    )
    adapt = fleet.adaptive_threshold()
    thresholds = {k: max(v, adapt) for k, v in thresholds.items()}

    # ── Stats val ─────────────────────────────────────────────────────────────
    n_val   = len(df_val)
    preds_v = fleet.predict(df_val, np.ones(n_val, dtype=bool))
    ctx_v   = classify_context_v4(df_val)
    sigs_v  = np.zeros(n_val, dtype=int)
    for i in range(n_val):
        thr = thresholds.get(str(ctx_v[i]), thresholds.get("general", adapt))
        if preds_v[i] >= thr:
            sigs_v[i] = 1
    if "regime_long" in df_val.columns:
        sigs_v[df_val["regime_long"].values == "NO_LONG"] = 0

    idx   = np.where(sigs_v == 1)[0]
    fut   = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns else np.zeros(n_val)
    gw    = sum(max(fut[i] - COST_PCT, 0) for i in idx)
    gl    = sum(max(COST_PCT - fut[i], 0) for i in idx)
    pf    = round(gw / max(gl, 1e-9), 3)
    wr    = round(sum(1 for i in idx if fut[i] > COST_PCT) / max(len(idx), 1), 3)
    elapsed = time.time() - t0

    meta = {
        "symbol":    symbol,
        "extra_sym": extra_sym,
        "train_end": TRAIN_END,
        "val_year":  VAL_YEAR,
        "n_features":len(feat_avail),
        "n_train":   int(tr_mask.sum()),
        "n_val":     int(val_mask.sum()),
        "val_n":     len(idx),
        "val_pf":    round(min(pf, 999.0), 3),
        "val_wr":    wr,
        "val_wr_pct":f"{wr*100:.0f}%",
        "elapsed_s": round(elapsed, 1),
        "trained_at":datetime.now(timezone.utc).isoformat(),
    }

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    joblib.dump(fleet, mp)
    tp.write_text(json.dumps(thresholds))
    _meta_path(symbol).write_text(json.dumps(meta, indent=2))

    print(f"  [{symbol}] ✓ train {elapsed:.0f}s  "
          f"val {VAL_YEAR}: n={len(idx)}  PF={pf:.3f}  WR={wr:.0%}  "
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
    symbol:       str,
    df_asset:     pd.DataFrame,
    df_btc:       pd.DataFrame,
    fleet:        TRMFleetLongV4,
    thresholds:   Dict[str, float],
    btc_vs_ema200:float,
    btc_regime:   str,
    state:        dict,
    dry_run:      bool = False,
) -> dict:
    now = datetime.now(timezone.utc)

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

    no_long   = regime_last == "NO_LONG"
    sup_block = not sup.allow
    raw       = p_last >= thr

    if no_long:
        action = "NO_SIGNAL"
    elif sup_block:
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
        "size_mult": round(sizing.final_size, 4),
        "close": round(close_val, 6), "blocked": False,
    }

    state["total_signals"] += 1

    if action == "LONG" and not dry_run:
        trade = {
            "entry_time": now.isoformat(), "symbol": symbol, "context": ctx,
            "p_long": round(p_last, 4), "threshold": round(thr, 4),
            "close_entry": round(close_val, 6), "regime_long": regime_last,
            "suppressor_level": sup.level, "suppressor_score": round(sup.score, 4),
            "size_multiplier": round(sizing.final_size, 4),
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
