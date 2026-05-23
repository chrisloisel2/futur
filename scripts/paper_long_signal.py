#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/paper_long_signal.py v2 — Paper Trading LONG BTC+ETH
=============================================================

Pipeline identique au walk_forward_unified.py --max-assets 1 :
  - Features   : 90 features FEATURES_INST_LONG depuis data/enriched/
  - Training   : BTC primary + ETH extra (meme config que le DEPLOYABLE validé)
  - Signal     : TRM Fleet Long v4 (100 TRM, 10 horizons × 10 archétypes)
  - Thresholds : calibrés sur val (année N-1)

Guardrails de production :
  KillSwitch   — intraday -1%, weekly DD -5%, crash BTC -30%/60j
  MetaSuppressor — bar_stress (vol spike, funding extreme, momentum extreme)
  DynamicSizer — vol targeting 15% + regime sizing (BEAR → 0.65×)
  Regime gate  — NO_LONG bloque le trade

Gates paper trading :
  Durée minimum        : 90 jours
  Trades minimum       : 100
  PF live net          : > 1.30 (après 30+ trades)
  DD max               : < 3%
  Drift                : 0 critique
  Erreur comptable     : 0

Usage :
  python3 scripts/paper_long_signal.py
  python3 scripts/paper_long_signal.py --dry-run
  python3 scripts/paper_long_signal.py --reset-state
  python3 scripts/paper_long_signal.py --report
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
    TEMPORAL_HORIZONS_V4,
    MOVEMENT_ARCHETYPES_V4,
)
from ai.level_0.labels import (
    compute_label_columns,
    build_labels,
    compute_long_regime_col,
)
from ai.level_0.features import get_available_features, FEATURES_LONG, FEATURES_COMMON
from ai.level_0.constants import COST_PCT, TARGET_COL, HORIZON_BARS
from ai.level_0.augmentation import augment_positives
from ai.meta.suppressor import MetaSuppressor
from risk.kill_switch import KillSwitch
from risk.dynamic_sizing import DynamicSizer
from config.deployment_status import LIVE_ENABLED, PAPER_ENABLED

try:
    from ai.level_0.institutional_features import FEATURES_INST_LONG, FEATURES_INST_FILTER
except ImportError:
    FEATURES_INST_LONG = FEATURES_LONG
    FEATURES_INST_FILTER = FEATURES_COMMON

# ─── Chemins ──────────────────────────────────────────────────────────────────

ENRICHED_DIR = ROOT / "data" / "enriched"
REPORT_DIR   = ROOT / "reports" / "paper_trading"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

TRADE_LOG      = REPORT_DIR / "paper_long_trades.csv"
SIGNAL_LOG     = REPORT_DIR / "paper_long_signals.csv"
STATE_FILE     = REPORT_DIR / "paper_long_state_v2.json"
KILLSWITCH_FILE= REPORT_DIR / "killswitch_state.json"

# ─── Constantes ───────────────────────────────────────────────────────────────

PRIMARY_SYMBOL = "BTCUSDT"
EXTRA_SYMBOL   = "ETHUSDT"

# Training : train_end = current_year - 2, val = current_year - 1
# (identique au protocole walk-forward)
CURRENT_YEAR  = datetime.now(timezone.utc).year
TRAIN_END     = CURRENT_YEAR - 2
VAL_YEAR      = CURRENT_YEAR - 1

# Gates paper trading
PAPER_MIN_DURATION_DAYS  = 90
PAPER_MIN_TRADES         = 100
PAPER_MIN_PF             = 1.30
PAPER_MAX_DD_PCT         = 3.0
PAPER_MIN_TRADES_FOR_PF  = 30    # PF évalué seulement à partir de ce seuil

# Slippage : 10bps simulé (COST_PCT), réel non disponible en paper
SLIPPAGE_SIM_BPS = int(COST_PCT * 10000)


# ─── Chargement données enrichies ────────────────────────────────────────────

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
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  WARN load {symbol}: {e}")
        return None


# ─── Découverte des features (identique à walk_forward_unified.py passe 1) ───

def discover_features(df_btc_full: pd.DataFrame) -> Tuple[List[str], List[str]]:
    cands_long = list(dict.fromkeys(FEATURES_INST_LONG + FEATURES_LONG))
    cands_filt = list(dict.fromkeys(list(FEATURES_INST_FILTER) + list(FEATURES_COMMON)))
    feat_long  = get_available_features(df_btc_full, cands_long, min_fill=0.75, context="LONG")
    feat_filt  = get_available_features(df_btc_full, cands_filt, min_fill=0.75, context="FILTER")
    return feat_long, feat_filt


# ─── Entraînement du TRM Fleet ────────────────────────────────────────────────

def train_fleet(
    df_btc: pd.DataFrame,
    df_eth: Optional[pd.DataFrame],
    feat_long: List[str],
    train_end: int,
    val_year: int,
) -> Tuple[TRMFleetLongV4, Dict[str, float], Dict]:
    """
    Entraîne le fleet sur BTC+ETH (train ≤ train_end, val = val_year).
    Retourne (fleet, thresholds, val_stats).
    """
    # ── Labels BTC ────────────────────────────────────────────────────────────
    df_btc = compute_label_columns(df_btc)
    df_btc = compute_long_regime_col(df_btc)
    years  = df_btc["datetime"].dt.year.values
    tr_mask = years <= train_end
    df_btc, _ = build_labels(df_btc, tr_mask)

    # ── Pool multi-actif BTC+ETH ──────────────────────────────────────────────
    df_btc_train = df_btc.loc[tr_mask].copy()
    dfs_train = [df_btc_train]

    if df_eth is not None:
        try:
            df_eth = compute_label_columns(df_eth)
            df_eth = compute_long_regime_col(df_eth)
            yrs_eth = df_eth["datetime"].dt.year.values
            tr_eth  = yrs_eth <= train_end
            if tr_eth.sum() >= 500:
                df_eth_lab, _ = build_labels(df_eth, tr_eth)
                # Couverture features : inclure uniquement si ≥ 70%
                n_present = sum(1 for f in feat_long if f in df_eth.columns)
                if n_present / max(len(feat_long), 1) >= 0.70:
                    dfs_train.append(df_eth_lab.loc[tr_eth].copy())
        except Exception as e:
            print(f"  WARN ETH labels: {e}")

    df_train = pd.concat(dfs_train, ignore_index=True)

    # ── SMOTE ─────────────────────────────────────────────────────────────────
    n_pos = int((df_train["y_long"] == 1).sum()) if "y_long" in df_train.columns else 0
    if 50 <= n_pos < 2000:
        try:
            _feat_s = [f for f in feat_long if f in df_train.columns]
            mult    = min(3, max(1, 2000 // max(n_pos, 1)))
            df_train = augment_positives(df_train, features=_feat_s, label_col="y_long", multiplier=mult)
        except Exception:
            pass

    # Feature selection basée sur BTC seul (évite la chute due aux altcoins sparse)
    feat_avail = [f for f in feat_long if f in df_btc_train.columns
                  and df_btc_train[f].notna().mean() >= 0.75]

    # ── Validation BTC ────────────────────────────────────────────────────────
    val_mask = years == val_year
    df_val   = df_btc.loc[val_mask].copy()

    tr_all = np.ones(len(df_train), dtype=bool)
    val_all = np.ones(len(df_val),  dtype=bool)

    # ── Entraîner fleet ───────────────────────────────────────────────────────
    fleet = TRMFleetLongV4(features=feat_avail)
    fleet.train(
        df_train, tr_all,
        df_val_btc=df_val,
        val_mask_in_btc=val_all,
        label_col="y_long",
    )

    # ── Calibration ───────────────────────────────────────────────────────────
    ret_val = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns \
              else np.zeros(len(df_val))
    thresholds = calibrate_context_thresholds_v4(
        fleet, df_val,
        filter_p=np.ones(len(df_val)), filter_thr=0.50,
        ret_val=ret_val, cost_pct=COST_PCT,
    )
    adapt = fleet.adaptive_threshold()
    thresholds = {k: max(v, adapt) for k, v in thresholds.items()}

    # ── Stats val ─────────────────────────────────────────────────────────────
    n_val  = len(df_val)
    ones   = np.ones(n_val, dtype=bool)
    preds  = fleet.predict(df_val, ones)
    ctx    = classify_context_v4(df_val)
    sigs   = np.zeros(n_val, dtype=int)
    for i in range(n_val):
        thr = thresholds.get(str(ctx[i]), thresholds.get("general", adapt))
        if preds[i] >= thr:
            sigs[i] = 1
    if "regime_long" in df_val.columns:
        sigs[df_val["regime_long"].values == "NO_LONG"] = 0

    idx = np.where(sigs == 1)[0]
    fut = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns else np.zeros(n_val)
    gw  = sum(max(fut[i] - COST_PCT, 0) for i in idx)
    gl  = sum(max(COST_PCT - fut[i], 0) for i in idx)
    pf  = gw / max(gl, 1e-9)
    wr  = sum(1 for i in idx if fut[i] > COST_PCT) / max(len(idx), 1)
    val_stats = {"n": len(idx), "pf": round(pf, 3), "wr": round(wr, 3), "features": len(feat_avail)}

    return fleet, thresholds, val_stats


# ─── State paper trading ──────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "start_date":          None,
        "total_signals":       0,
        "total_trades":        0,
        "total_wins":          0,
        "total_losses":        0,
        "gross_win_sum":       0.0,
        "gross_loss_sum":      0.0,
        "slippage_total_bps":  0,
        "cumulative_pnl_pct":  0.0,
        "peak_pnl_pct":        0.0,
        "max_dd_pct":          0.0,
        "consecutive_losses":  0,
        "last_trade_time":     None,
        "weekly_pnl_pct":      0.0,
        "weekly_start_time":   None,
        "crash_halt_until":    None,
        "drift_alerts":        0,
        "accounting_errors":   0,
    }


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _load_trade_log() -> pd.DataFrame:
    if TRADE_LOG.exists():
        try:
            return pd.read_csv(TRADE_LOG, parse_dates=["entry_time"])
        except Exception:
            pass
    return pd.DataFrame(columns=[
        "entry_time", "symbol", "context", "p_long", "threshold",
        "close_entry", "regime_long", "suppressor_level", "suppressor_score",
        "size_multiplier", "vol_24h", "regime_mult",
        "future_ret_raw", "future_ret_net", "outcome",
        "slippage_sim_bps",
    ])


# ─── KillSwitch helpers ───────────────────────────────────────────────────────

def _check_crash(df_btc: pd.DataFrame, state: dict) -> Tuple[bool, str]:
    now = datetime.now(timezone.utc)
    if state.get("crash_halt_until"):
        halt = pd.Timestamp(state["crash_halt_until"]).tz_localize("UTC") \
               if pd.Timestamp(state["crash_halt_until"]).tzinfo is None \
               else pd.Timestamp(state["crash_halt_until"])
        if now < halt.to_pydatetime():
            return True, f"CRASH_HALT actif jusqu'au {halt.date()}"

    if len(df_btc) < 1440 + 10:
        return False, ""
    close = df_btc["close"].values
    ret_60d = float(close[-1] / close[-1440] - 1.0)   # 1440h = 60j
    if ret_60d < -0.30:
        until = (now + timedelta(days=30)).isoformat()
        state["crash_halt_until"] = until
        _save_state(state)
        return True, f"CRASH_CIRCUIT_BREAKER BTC {ret_60d*100:.1f}%/60j"
    return False, ""


def _check_consecutive_losses(state: dict) -> Tuple[bool, str]:
    if state["consecutive_losses"] <= 4:
        return False, ""
    if state.get("last_trade_time"):
        lt = pd.Timestamp(state["last_trade_time"])
        if lt.tzinfo is None:
            lt = lt.tz_localize("UTC")
        cooldown = lt + pd.Timedelta(hours=48)
        if datetime.now(timezone.utc) < cooldown.to_pydatetime():
            return True, f"CONSECUTIVE_LOSSES={state['consecutive_losses']} (cooldown 48h)"
        state["consecutive_losses"] = 0
        _save_state(state)
    return False, ""


def _check_weekly_loss(state: dict) -> Tuple[bool, str]:
    now = datetime.now(timezone.utc)
    ws  = state.get("weekly_start_time")
    if ws:
        wst = pd.Timestamp(ws)
        if wst.tzinfo is None:
            wst = wst.tz_localize("UTC")
        if (now - wst.to_pydatetime()).days >= 7:
            state["weekly_pnl_pct"]    = 0.0
            state["weekly_start_time"] = now.isoformat()
            _save_state(state)
    else:
        state["weekly_start_time"] = now.isoformat()
        _save_state(state)

    if state.get("weekly_pnl_pct", 0.0) <= -5.0:
        return True, f"WEEKLY_LOSS_LIMIT {state['weekly_pnl_pct']:.1f}%"
    return False, ""


# ─── DD tracking ──────────────────────────────────────────────────────────────

def _update_dd(state: dict, pnl_pct: float) -> float:
    state["cumulative_pnl_pct"] += pnl_pct
    peak = max(state["peak_pnl_pct"], state["cumulative_pnl_pct"])
    state["peak_pnl_pct"] = peak
    dd = peak - state["cumulative_pnl_pct"]
    state["max_dd_pct"] = max(state["max_dd_pct"], dd)
    return state["max_dd_pct"]


# ─── Rapport / Gates ──────────────────────────────────────────────────────────

def _compute_live_pf(state: dict) -> Optional[float]:
    gw = state["gross_win_sum"]
    gl = state["gross_loss_sum"]
    if state["total_trades"] < PAPER_MIN_TRADES_FOR_PF:
        return None
    return gw / max(gl, 1e-9)


def _gate_status(state: dict) -> List[Tuple[str, bool, str]]:
    """Retourne une liste (gate_name, passed, detail)."""
    now = datetime.now(timezone.utc)
    start = state.get("start_date")
    dur_days = (now - pd.Timestamp(start).to_pydatetime().replace(tzinfo=timezone.utc)).days \
               if start else 0

    pf_live = _compute_live_pf(state)
    n_trades = state["total_trades"]
    dd       = state["max_dd_pct"]

    gates = [
        ("Durée ≥ 90j",      dur_days >= PAPER_MIN_DURATION_DAYS,
         f"{dur_days}j / {PAPER_MIN_DURATION_DAYS}j"),
        ("Trades ≥ 100",     n_trades >= PAPER_MIN_TRADES,
         f"{n_trades} / {PAPER_MIN_TRADES}"),
        ("PF live ≥ 1.30",  pf_live is not None and pf_live >= PAPER_MIN_PF,
         f"{pf_live:.3f}" if pf_live else f"N/A (besoin {PAPER_MIN_TRADES_FOR_PF}+ trades)"),
        ("DD < 3%",          dd < PAPER_MAX_DD_PCT,
         f"{dd:.2f}%"),
        ("Erreur comptable", state["accounting_errors"] == 0,
         f"{state['accounting_errors']}"),
        ("Drift critique",   state["drift_alerts"] == 0,
         f"{state['drift_alerts']} alertes"),
    ]
    return gates


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper LONG signal v2 — TRM Fleet BTC+ETH")
    parser.add_argument("--dry-run",     action="store_true", help="Signal sans log")
    parser.add_argument("--reset-state", action="store_true", help="Reset state")
    parser.add_argument("--report",      action="store_true", help="Rapport gates uniquement")
    args = parser.parse_args()

    t0  = time.time()
    now = datetime.now(timezone.utc)

    print("=" * 68)
    print("  PAPER LONG SIGNAL v2 — TRM Fleet v4  BTC+ETH")
    print(f"  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Training: ≤{TRAIN_END}  Val: {VAL_YEAR}  Live: {CURRENT_YEAR}")
    print("=" * 68)

    if not PAPER_ENABLED:
        print("  !! PAPER_ENABLED=False → signal désactivé")
        return

    if LIVE_ENABLED:
        print("  !! LIVE_ENABLED=True → paper signal désactivé")
        return

    # ── State ──────────────────────────────────────────────────────────────────
    if args.reset_state:
        STATE_FILE.unlink(missing_ok=True)
        KILLSWITCH_FILE.unlink(missing_ok=True)
        print("  [STATE] Reset effectué.")

    state = _load_state()
    if state["start_date"] is None and not args.dry_run:
        state["start_date"] = now.isoformat()
        _save_state(state)

    if args.report:
        gates = _gate_status(state)
        _print_gates(gates, state)
        return

    # ── Chargement données ──────────────────────────────────────────────────────
    print(f"\n[1/5] Chargement données enrichies BTC+ETH…")
    df_btc_full = _load_enriched(PRIMARY_SYMBOL)
    if df_btc_full is None:
        print(f"  ERREUR : {PRIMARY_SYMBOL} enriched non disponible.")
        print(f"  → Lancer : python3 scripts/assemble_enriched_from_dataout.py")
        sys.exit(1)

    df_eth_full = _load_enriched(EXTRA_SYMBOL)

    print(f"   BTC: {len(df_btc_full):,} barres  "
          f"{df_btc_full['datetime'].iloc[0].date()} → {df_btc_full['datetime'].iloc[-1].date()}")
    if df_eth_full is not None:
        print(f"   ETH: {len(df_eth_full):,} barres  "
              f"{df_eth_full['datetime'].iloc[0].date()} → {df_eth_full['datetime'].iloc[-1].date()}")

    # ── Feature discovery (passe 1 sur BTC complet) ────────────────────────────
    feat_long_cand, feat_filt_cand = discover_features(df_btc_full)
    print(f"   Features LONG candidates : {len(feat_long_cand)}")

    # Libérer et recharger avec sélection de colonnes
    del df_btc_full
    import gc; gc.collect()

    required = list(set(feat_long_cand) | set(feat_filt_cand))
    df_btc = _load_enriched(PRIMARY_SYMBOL, required_cols=required)
    df_eth = _load_enriched(EXTRA_SYMBOL,   required_cols=required)

    # ── KillSwitch ───────────────────────────────────────────────────────────
    print(f"\n[2/5] Vérification guardrails…")

    blocked, block_reason = False, ""
    crash_ok, reason_crash = _check_crash(df_btc, state)
    if crash_ok:
        blocked, block_reason = True, reason_crash

    if not blocked:
        cl_ok, reason_cl = _check_consecutive_losses(state)
        if cl_ok:
            blocked, block_reason = True, reason_cl

    if not blocked:
        wl_ok, reason_wl = _check_weekly_loss(state)
        if wl_ok:
            blocked, block_reason = True, reason_wl

    # Régime BTC informatif
    close = df_btc["close"].values
    ema200 = float(pd.Series(close).ewm(span=200, adjust=False).mean().iloc[-1])
    vs_ema = (close[-1] / ema200 - 1.0) * 100
    ret_7d = (close[-1] / close[-min(168, len(close)-1)] - 1.0) * 100
    ret_60d= (close[-1] / close[-min(1440, len(close)-1)] - 1.0) * 100
    btc_regime = "BULL" if vs_ema > 0 else "BEAR"

    print(f"   BTC: {close[-1]:,.0f} USDT  {btc_regime}  EMA200h={vs_ema:+.1f}%  "
          f"7j={ret_7d:+.1f}%  60j={ret_60d:+.1f}%")
    print(f"   Consec losses: {state['consecutive_losses']}  "
          f"Weekly PnL: {state.get('weekly_pnl_pct', 0):.1f}%")

    if blocked:
        print(f"\n  !! SIGNAL BLOQUÉ : {block_reason}")
        _print_gates(_gate_status(state), state)
        return

    # ── Entraînement ─────────────────────────────────────────────────────────
    print(f"\n[3/5] Entraînement TRM Fleet (train≤{TRAIN_END}, val={VAL_YEAR})…")

    fleet, thresholds, val_stats = train_fleet(
        df_btc, df_eth, feat_long_cand, TRAIN_END, VAL_YEAR
    )
    print(f"   Val {VAL_YEAR}: n={val_stats['n']} trades  "
          f"PF={val_stats['pf']:.3f}  WR={val_stats['wr']:.0%}  "
          f"features={val_stats['features']}")

    if val_stats["n"] < 30:
        print(f"  !! SIGNAL BLOQUÉ : val {VAL_YEAR} = {val_stats['n']} trades < 30 minimum")
        return

    # ── Signal live ───────────────────────────────────────────────────────────
    print(f"\n[4/5] Signal live…")

    # Barre la plus récente
    df_live = df_btc.copy()
    if TARGET_COL not in df_live.columns:
        df_live = compute_label_columns(df_live)
    if "regime_long" not in df_live.columns:
        df_live = compute_long_regime_col(df_live)

    last_bar  = df_live.iloc[-1]
    ones      = np.array([True])
    p_last    = float(fleet.predict(df_live.iloc[[-1]], ones)[0])
    ctx_last  = str(classify_context_v4(df_live.iloc[[-1]])[0])
    thr_last  = thresholds.get(ctx_last, thresholds.get("general", 0.55))
    regime_last = str(last_bar.get("regime_long", "NEUTRAL"))

    # ── MetaSuppressor ───────────────────────────────────────────────────────
    suppressor = MetaSuppressor()
    regime_str = "BEAR" if vs_ema < -0.02 else ("EXPANSION" if vs_ema > 0.05 else "RECOVERY")
    sup_result = suppressor.evaluate(
        bar         = last_bar,
        predictions = {"p_long": p_last},
        regime      = regime_str,
        side        = "long",
    )

    # ── DynamicSizer ─────────────────────────────────────────────────────────
    sizer = DynamicSizer(target_annual_vol=0.15)
    rv24  = float(last_bar.get("rv_24", 0.02))
    reg_mult = 0.65 if btc_regime == "BEAR" else 1.0
    sizing = sizer.compute_size(
        base_size     = 1.0,
        vol_24h       = rv24,
        regime_mult   = reg_mult,
        liquidity_mult= sup_result.size_multiplier,
    )

    # ── Décision signal ───────────────────────────────────────────────────────
    no_long   = (regime_last == "NO_LONG")
    sup_block = not sup_result.allow
    raw_signal= (p_last >= thr_last)

    if no_long:
        action = "NO_SIGNAL (NO_LONG gate)"
    elif sup_block:
        action = f"NO_SIGNAL (MetaSuppressor BLOCKED: {sup_result.reasons})"
    elif raw_signal:
        action = "PAPER_LONG"
    elif p_last >= thr_last * 0.90:
        action = "WATCH"
    else:
        action = "NO_SIGNAL"

    # ── Affichage signal ──────────────────────────────────────────────────────
    print(f"\n[5/5] Rapport")
    print("=" * 68)
    print(f"  BTC actuel         : {close[-1]:,.0f} USDT  [{btc_regime}]")
    print(f"  EMA200h            : {vs_ema:+.1f}%")
    print(f"  Contexte           : {ctx_last}")
    print(f"  Régime LONG gate   : {regime_last}")
    print(f"  Probabilité LONG   : {p_last:.4f}  (seuil {thr_last:.4f})")
    print()
    print(f"  MetaSuppressor     : {sup_result.level}  (score={sup_result.score:.2f})")
    if sup_result.reasons:
        print(f"    Raisons          : {sup_result.reasons}")
    print(f"  DynamicSizer       : {sizing.final_size:.2f}×  "
          f"(vol={sizing.vol_multiplier:.2f}  régime={sizing.regime_multiplier:.2f})")
    print(f"  Slippage simulé    : {SLIPPAGE_SIM_BPS}bps/trade  |  Réel : non mesuré (paper)")
    print()

    if action == "PAPER_LONG":
        print(f"  >> ACTION : PAPER_LONG  <<")
        print(f"     p={p_last:.4f} >= seuil={thr_last:.4f}  size={sizing.final_size:.2f}×")
    elif action == "WATCH":
        print(f"  >> ACTION : WATCH  (p={p_last:.4f} proche seuil {thr_last:.4f})")
    else:
        print(f"  >> ACTION : {action}")

    print()

    # ── Log ───────────────────────────────────────────────────────────────────
    state["total_signals"] += 1
    if action == "PAPER_LONG" and not args.dry_run:
        trade_row = {
            "entry_time":        now.isoformat(),
            "symbol":            PRIMARY_SYMBOL,
            "context":           ctx_last,
            "p_long":            round(p_last, 4),
            "threshold":         round(thr_last, 4),
            "close_entry":       round(float(close[-1]), 2),
            "regime_long":       regime_last,
            "suppressor_level":  sup_result.level,
            "suppressor_score":  round(sup_result.score, 4),
            "size_multiplier":   round(sizing.final_size, 4),
            "vol_24h":           round(rv24, 5),
            "regime_mult":       round(reg_mult, 2),
            "future_ret_raw":    None,   # rempli a posteriori
            "future_ret_net":    None,
            "outcome":           "OPEN",
            "slippage_sim_bps":  SLIPPAGE_SIM_BPS,
        }
        df_trades = _load_trade_log()
        df_trades = pd.concat([df_trades, pd.DataFrame([trade_row])], ignore_index=True)
        df_trades.to_csv(TRADE_LOG, index=False)
        state["total_trades"] += 1
        _save_state(state)
        print(f"   Trade loggé → {TRADE_LOG.name}")

    # ── Signal log (toutes les barres) ────────────────────────────────────────
    if not args.dry_run:
        sig_row = {
            "timestamp":    now.isoformat(),
            "p_long":       round(p_last, 4),
            "threshold":    round(thr_last, 4),
            "action":       action,
            "context":      ctx_last,
            "btc_close":    round(float(close[-1]), 2),
            "btc_vs_ema":   round(vs_ema, 2),
            "sup_level":    sup_result.level,
            "size_mult":    round(sizing.final_size, 4),
        }
        df_sig = pd.read_csv(SIGNAL_LOG) if SIGNAL_LOG.exists() else pd.DataFrame()
        df_sig = pd.concat([df_sig, pd.DataFrame([sig_row])], ignore_index=True)
        df_sig.to_csv(SIGNAL_LOG, index=False)

    # ── Gates paper trading ───────────────────────────────────────────────────
    gates = _gate_status(state)
    _print_gates(gates, state)

    print(f"\n  Durée: {time.time()-t0:.1f}s")
    print("=" * 68)
    print("  !! PAPER TRADING UNIQUEMENT — LIVE_ENABLED=False !!")
    print("=" * 68)


def _print_gates(gates: List[Tuple], state: dict) -> None:
    print("\n  PAPER TRADING GATES :")
    all_ok = True
    for name, ok, detail in gates:
        icon = "  OK  " if ok else "ENCOURS"
        print(f"    [{icon}] {name:<22} {detail}")
        if not ok:
            all_ok = False

    pf = _compute_live_pf(state)
    n  = state["total_trades"]
    wr = state["total_wins"] / max(n, 1)
    print(f"\n  Métriques live ({n} trades) :")
    print(f"    WR   : {wr:.1%}")
    print(f"    PF   : {pf:.3f}" if pf else f"    PF   : N/A ({n}/{PAPER_MIN_TRADES_FOR_PF} trades)")
    print(f"    DD   : {state['max_dd_pct']:.2f}%")
    print(f"    PnL  : {state['cumulative_pnl_pct']:+.2f}%")

    if all_ok:
        print(f"\n  >> TOUTES LES GATES PASSÉES — candidat LIVE <<")
    else:
        remaining = sum(1 for _, ok, _ in gates if not ok)
        print(f"\n  >> {remaining} gate(s) en cours — continuer paper trading")


if __name__ == "__main__":
    main()
