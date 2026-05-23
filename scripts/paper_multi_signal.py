#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/paper_multi_signal.py — Paper Trading Multi-Asset Fleet
================================================================

Stratégie : entraîne le TRM Fleet UNE FOIS sur BTC+ETH,
puis applique l'inférence sur chaque asset du TOP 10.

Architecture :
  Train   : BTC primary + ETH extra (identique à paper_long_signal.py)
  Inférence : 10 assets en séquence (~2s/asset après le train)
  Régime  : BTC EMA200 comme gate globale de marché
  Guardrails : KillSwitch + MetaSuppressor + DynamicSizer per asset

Sorties :
  reports/paper_trading/{SYMBOL}/state.json
  reports/paper_trading/{SYMBOL}/signals.csv
  reports/paper_trading/{SYMBOL}/trades.csv
  reports/paper_trading/fleet_summary.json

Usage :
  python3 scripts/paper_multi_signal.py
  python3 scripts/paper_multi_signal.py --dry-run
  python3 scripts/paper_multi_signal.py --reset-state
  python3 scripts/paper_multi_signal.py --symbols BTCUSDT SOLUSDT
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
from ai.level_0.constants import COST_PCT, TARGET_COL
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

# ─── Configuration ────────────────────────────────────────────────────────────

ENRICHED_DIR  = ROOT / "data" / "enriched"
FLEET_DIR     = ROOT / "reports" / "paper_trading"
FLEET_DIR.mkdir(parents=True, exist_ok=True)

FLEET_SUMMARY = FLEET_DIR / "fleet_summary.json"

PRIMARY_SYMBOL = "BTCUSDT"
EXTRA_SYMBOL   = "ETHUSDT"

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]

CURRENT_YEAR = datetime.now(timezone.utc).year
TRAIN_END    = CURRENT_YEAR - 2
VAL_YEAR     = CURRENT_YEAR - 1

PAPER_MIN_DURATION_DAYS = 90
PAPER_MIN_TRADES        = 100
PAPER_MIN_PF            = 1.30
PAPER_MAX_DD_PCT        = 3.0
PAPER_MIN_TRADES_FOR_PF = 30
SLIPPAGE_SIM_BPS        = int(COST_PCT * 10000)


# ─── Helpers data ─────────────────────────────────────────────────────────────

def _asset_dir(symbol: str) -> Path:
    d = FLEET_DIR / symbol
    d.mkdir(parents=True, exist_ok=True)
    return d


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


# ─── Training (identique à paper_long_signal) ────────────────────────────────

def train_fleet_btc_eth(
    df_btc: pd.DataFrame,
    df_eth: Optional[pd.DataFrame],
    feat_long: List[str],
) -> Tuple[TRMFleetLongV4, Dict[str, float], dict]:

    df_btc = compute_label_columns(df_btc)
    df_btc = compute_long_regime_col(df_btc)
    years  = df_btc["datetime"].dt.year.values
    tr_mask = years <= TRAIN_END
    df_btc, _ = build_labels(df_btc, tr_mask)

    df_btc_train = df_btc.loc[tr_mask].copy()
    dfs_train = [df_btc_train]

    if df_eth is not None:
        try:
            df_eth = compute_label_columns(df_eth)
            df_eth = compute_long_regime_col(df_eth)
            yrs_eth = df_eth["datetime"].dt.year.values
            tr_eth  = yrs_eth <= TRAIN_END
            if tr_eth.sum() >= 500:
                df_eth_lab, _ = build_labels(df_eth, tr_eth)
                n_present = sum(1 for f in feat_long if f in df_eth.columns)
                if n_present / max(len(feat_long), 1) >= 0.70:
                    dfs_train.append(df_eth_lab.loc[tr_eth].copy())
        except Exception:
            pass

    df_train = pd.concat(dfs_train, ignore_index=True)

    n_pos = int((df_train.get("y_long", pd.Series(dtype=int)) == 1).sum()) \
            if "y_long" in df_train.columns else 0
    if 50 <= n_pos < 2000:
        try:
            _fs = [f for f in feat_long if f in df_train.columns]
            mult = min(3, max(1, 2000 // max(n_pos, 1)))
            df_train = augment_positives(df_train, features=_fs, label_col="y_long", multiplier=mult)
        except Exception:
            pass

    feat_avail = [f for f in feat_long if f in df_btc_train.columns
                  and df_btc_train[f].notna().mean() >= 0.75]

    val_mask = years == VAL_YEAR
    df_val   = df_btc.loc[val_mask].copy()

    fleet = TRMFleetLongV4(features=feat_avail)
    fleet.train(
        df_train, np.ones(len(df_train), dtype=bool),
        df_val_btc=df_val,
        val_mask_in_btc=np.ones(len(df_val), dtype=bool),
        label_col="y_long",
    )

    ret_val = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns \
              else np.zeros(len(df_val))
    thresholds = calibrate_context_thresholds_v4(
        fleet, df_val,
        filter_p=np.ones(len(df_val)), filter_thr=0.50,
        ret_val=ret_val, cost_pct=COST_PCT,
    )
    adapt = fleet.adaptive_threshold()
    thresholds = {k: max(v, adapt) for k, v in thresholds.items()}

    # Val stats
    n_val  = len(df_val)
    preds  = fleet.predict(df_val, np.ones(n_val, dtype=bool))
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
    val_stats = {
        "n": len(idx),
        "pf": round(gw / max(gl, 1e-9), 3),
        "wr": round(sum(1 for i in idx if fut[i] > COST_PCT) / max(len(idx), 1), 3),
        "features": len(feat_avail),
    }
    return fleet, thresholds, val_stats


# ─── State per asset ──────────────────────────────────────────────────────────

def _load_state(symbol: str) -> dict:
    f = _asset_dir(symbol) / "state.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {
        "symbol": symbol,
        "start_date": None,
        "total_signals": 0,
        "total_trades": 0,
        "total_wins": 0,
        "total_losses": 0,
        "gross_win_sum": 0.0,
        "gross_loss_sum": 0.0,
        "cumulative_pnl_pct": 0.0,
        "peak_pnl_pct": 0.0,
        "max_dd_pct": 0.0,
        "consecutive_losses": 0,
        "last_trade_time": None,
        "weekly_pnl_pct": 0.0,
        "weekly_start_time": None,
        "crash_halt_until": None,
        "drift_alerts": 0,
        "accounting_errors": 0,
    }


def _save_state(symbol: str, state: dict) -> None:
    f = _asset_dir(symbol) / "state.json"
    f.write_text(json.dumps(state, indent=2, default=str))


def _append_signal_log(symbol: str, row: dict) -> None:
    f = _asset_dir(symbol) / "signals.csv"
    df = pd.read_csv(f) if f.exists() else pd.DataFrame()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(f, index=False)


def _append_trade_log(symbol: str, row: dict) -> None:
    f = _asset_dir(symbol) / "trades.csv"
    df = pd.read_csv(f) if f.exists() else pd.DataFrame()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(f, index=False)


# ─── KillSwitch checks ────────────────────────────────────────────────────────

def _check_guards(symbol: str, state: dict, df_btc: pd.DataFrame, df_asset: pd.DataFrame) -> Tuple[bool, str]:
    now = datetime.now(timezone.utc)

    # Crash BTC (global gate)
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
            until = (now + timedelta(days=30)).isoformat()
            state["crash_halt_until"] = until
            _save_state(symbol, state)
            return True, f"CRASH_CIRCUIT_BREAKER BTC {ret_60d*100:.1f}%/60j"

    # Pertes consécutives asset
    if state["consecutive_losses"] > 4 and state.get("last_trade_time"):
        lt = pd.Timestamp(state["last_trade_time"])
        if lt.tzinfo is None:
            lt = lt.tz_localize("UTC")
        if now < (lt + pd.Timedelta(hours=48)).to_pydatetime():
            return True, f"CONSEC_LOSSES={state['consecutive_losses']} (cooldown 48h)"
        state["consecutive_losses"] = 0
        _save_state(symbol, state)

    # DD hebdo asset
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
        return True, f"WEEKLY_LOSS_LIMIT {state['weekly_pnl_pct']:.1f}%"

    return False, ""


# ─── Signal par asset ─────────────────────────────────────────────────────────

def run_asset_signal(
    symbol: str,
    df_asset: pd.DataFrame,
    df_btc: pd.DataFrame,
    fleet: TRMFleetLongV4,
    thresholds: Dict[str, float],
    btc_vs_ema200: float,
    btc_regime: str,
    state: dict,
    dry_run: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)

    if state["start_date"] is None and not dry_run:
        state["start_date"] = now.isoformat()
        _save_state(symbol, state)

    # Guardrails
    blocked, block_reason = _check_guards(symbol, state, df_btc, df_asset)

    # Labels + régime sur l'asset
    try:
        df_live = compute_label_columns(df_asset.copy())
        df_live = compute_long_regime_col(df_live)
    except Exception:
        df_live = df_asset.copy()

    last_bar  = df_live.iloc[-1]
    close_val = float(last_bar.get("close", last_bar.get("Close", 0.0)))

    if blocked:
        sig_result = {
            "symbol": symbol, "timestamp": now.isoformat(),
            "action": f"BLOCKED ({block_reason})", "p_long": None,
            "threshold": None, "btc_regime": btc_regime,
            "close": round(close_val, 4), "blocked": True,
        }
        state["total_signals"] += 1
        if not dry_run:
            _append_signal_log(symbol, sig_result)
            _save_state(symbol, state)
        return sig_result

    # Prédiction
    ones   = np.array([True])
    p_last = float(fleet.predict(df_live.iloc[[-1]], ones)[0])
    ctx    = str(classify_context_v4(df_live.iloc[[-1]])[0])
    thr    = thresholds.get(ctx, thresholds.get("general", 0.55))
    regime_last = str(last_bar.get("regime_long", "NEUTRAL"))

    # MetaSuppressor
    suppressor = MetaSuppressor()
    regime_str = "BEAR" if btc_vs_ema200 < -0.02 else ("EXPANSION" if btc_vs_ema200 > 0.05 else "RECOVERY")
    sup = suppressor.evaluate(
        bar=last_bar,
        predictions={"p_long": p_last},
        regime=regime_str,
        side="long",
    )

    # DynamicSizer
    sizer   = DynamicSizer(target_annual_vol=0.15)
    rv24    = float(last_bar.get("rv_24", 0.02))
    reg_mult = 0.65 if btc_regime == "BEAR" else 1.0
    sizing  = sizer.compute_size(
        base_size=1.0, vol_24h=rv24,
        regime_mult=reg_mult,
        liquidity_mult=sup.size_multiplier,
    )

    # Décision
    no_long   = (regime_last == "NO_LONG")
    sup_block = not sup.allow
    raw       = (p_last >= thr)

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

    sig_result = {
        "symbol":      symbol,
        "timestamp":   now.isoformat(),
        "action":      action,
        "p_long":      round(p_last, 4),
        "threshold":   round(thr, 4),
        "context":     ctx,
        "regime_long": regime_last,
        "btc_regime":  btc_regime,
        "sup_level":   sup.level,
        "sup_score":   round(sup.score, 4),
        "size_mult":   round(sizing.final_size, 4),
        "close":       round(close_val, 6),
        "blocked":     False,
    }

    state["total_signals"] += 1

    if action == "LONG" and not dry_run:
        trade_row = {
            "entry_time":       now.isoformat(),
            "symbol":           symbol,
            "context":          ctx,
            "p_long":           round(p_last, 4),
            "threshold":        round(thr, 4),
            "close_entry":      round(close_val, 6),
            "regime_long":      regime_last,
            "suppressor_level": sup.level,
            "suppressor_score": round(sup.score, 4),
            "size_multiplier":  round(sizing.final_size, 4),
            "vol_24h":          round(rv24, 5),
            "regime_mult":      round(reg_mult, 2),
            "future_ret_raw":   None,
            "future_ret_net":   None,
            "outcome":          "OPEN",
            "slippage_sim_bps": SLIPPAGE_SIM_BPS,
        }
        _append_trade_log(symbol, trade_row)
        state["total_trades"] += 1

    if not dry_run:
        _append_signal_log(symbol, sig_result)
        _save_state(symbol, state)

    return sig_result


# ─── Gates ───────────────────────────────────────────────────────────────────

def _compute_gates(state: dict) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = state.get("start_date")
    dur_days = 0
    if start:
        try:
            st = pd.Timestamp(start)
            if st.tzinfo is None:
                st = st.tz_localize("UTC")
            dur_days = max(0, (now - st.to_pydatetime()).days)
        except Exception:
            pass

    n_trades = state.get("total_trades", 0)
    n_wins   = state.get("total_wins", 0)
    gw       = state.get("gross_win_sum", 0.0)
    gl       = state.get("gross_loss_sum", 0.0)
    dd       = state.get("max_dd_pct", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= PAPER_MIN_TRADES_FOR_PF else None

    return [
        {"name": "Durée ≥ 90j",    "ok": dur_days >= 90,  "value": dur_days,      "target": 90,   "unit": "j"},
        {"name": "Trades ≥ 100",   "ok": n_trades >= 100, "value": n_trades,      "target": 100,  "unit": ""},
        {"name": "PF live ≥ 1.30", "ok": pf is not None and pf >= 1.30,
         "value": pf,              "target": 1.30, "unit": ""},
        {"name": "DD < 3%",        "ok": dd < 3.0,        "value": round(dd, 2),  "target": 3.0,  "unit": "%"},
        {"name": "Erreurs",        "ok": state.get("accounting_errors", 0) == 0,
         "value": state.get("accounting_errors", 0), "target": 0, "unit": ""},
        {"name": "Drift",          "ok": state.get("drift_alerts", 0) == 0,
         "value": state.get("drift_alerts", 0),      "target": 0, "unit": ""},
    ]


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Multi-Asset Signal — TRM Fleet v4")
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Sous-ensemble d'assets (défaut: TOP_10 disponibles)")
    args = parser.parse_args()

    t0  = time.time()
    now = datetime.now(timezone.utc)

    print("=" * 68)
    print("  PAPER MULTI-ASSET SIGNAL — TRM Fleet v4")
    print(f"  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Train ≤{TRAIN_END}  Val {VAL_YEAR}  TOP_10")
    print("=" * 68)

    if not PAPER_ENABLED:
        print("  PAPER_ENABLED=False → désactivé")
        return

    # Assets disponibles
    target = args.symbols if args.symbols else TOP_10
    available = [s for s in target if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    missing   = [s for s in target if s not in available]

    if missing:
        print(f"\n  MANQUANTS (bootstrap requis) : {', '.join(missing)}")
        print(f"  → python3 scripts/bootstrap_enriched.py --symbols {' '.join(missing)}")

    if not available:
        print("  Aucun asset disponible. Abandon.")
        sys.exit(1)

    print(f"\n  Assets : {', '.join(available)} ({len(available)}/{len(target)})")

    # Reset states
    if args.reset_state:
        for sym in available:
            for f in (_asset_dir(sym) / "state.json",):
                if f.exists():
                    f.unlink()
        print("  [RESET] States effacés.")

    # ── Phase 1 : Chargement BTC+ETH ──────────────────────────────────────────
    print(f"\n[1/3] Chargement BTC+ETH + feature discovery…")
    df_btc_full = _load_enriched(PRIMARY_SYMBOL)
    if df_btc_full is None:
        print("  ERREUR : BTCUSDT enriched manquant. Lancer live_data_update.py d'abord.")
        sys.exit(1)

    df_eth_full = _load_enriched(EXTRA_SYMBOL)

    cands_long = list(dict.fromkeys(FEATURES_INST_LONG + FEATURES_LONG))
    feat_long  = get_available_features(df_btc_full, cands_long, min_fill=0.75, context="LONG")
    print(f"   Features LONG : {len(feat_long)}  |  BTC: {len(df_btc_full):,} barres")

    del df_btc_full
    import gc; gc.collect()

    required = list(set(feat_long))
    df_btc = _load_enriched(PRIMARY_SYMBOL, required_cols=required)
    df_eth = _load_enriched(EXTRA_SYMBOL,   required_cols=required)

    # BTC regime (global)
    close_btc   = df_btc["close"].values
    ema200_btc  = float(pd.Series(close_btc).ewm(span=200, adjust=False).mean().iloc[-1])
    btc_vs_ema  = (close_btc[-1] / ema200_btc - 1.0) * 100
    btc_regime  = "BULL" if btc_vs_ema > 0 else "BEAR"
    ret_7d_btc  = (close_btc[-1] / close_btc[-min(168, len(close_btc)-1)] - 1.0) * 100

    print(f"   BTC: {close_btc[-1]:,.0f} USDT  [{btc_regime}]  EMA200={btc_vs_ema:+.1f}%  7j={ret_7d_btc:+.1f}%")

    # ── Phase 2 : Entraînement (une seule fois) ────────────────────────────────
    print(f"\n[2/3] Entraînement TRM Fleet sur BTC+ETH (train≤{TRAIN_END}, val={VAL_YEAR})…")
    fleet, thresholds, val_stats = train_fleet_btc_eth(df_btc, df_eth, feat_long)
    print(f"   Val {VAL_YEAR}: n={val_stats['n']}  PF={val_stats['pf']:.3f}  "
          f"WR={val_stats['wr']:.0%}  features={val_stats['features']}")

    if val_stats["n"] < 20:
        print(f"  !! Validation trop faible ({val_stats['n']} trades) — abandon")
        sys.exit(1)

    # ── Phase 3 : Inférence sur chaque asset ──────────────────────────────────
    print(f"\n[3/3] Inférence multi-asset…")
    fleet_results = []
    missing_parquet = []

    for sym in available:
        t_sym = time.time()
        df_asset = _load_enriched(sym, required_cols=required)
        if df_asset is None:
            missing_parquet.append(sym)
            continue

        state = _load_state(sym)
        try:
            result = run_asset_signal(
                sym, df_asset, df_btc, fleet, thresholds,
                btc_vs_ema / 100.0, btc_regime, state,
                dry_run=args.dry_run,
            )
        except Exception as e:
            result = {
                "symbol": sym, "timestamp": now.isoformat(),
                "action": "ERROR", "p_long": None, "threshold": None,
                "btc_regime": btc_regime, "close": None, "error": str(e),
            }
            print(f"   [{sym}] ERREUR : {e}")

        # Enrichir avec gates
        state_fresh = _load_state(sym)
        gates = _compute_gates(state_fresh)
        gates_ok = all(g["ok"] for g in gates)
        n_t = state_fresh.get("total_trades", 0)
        gw  = state_fresh.get("gross_win_sum", 0.0)
        gl  = state_fresh.get("gross_loss_sum", 0.0)
        pf  = round(gw / max(gl, 1e-9), 3) if n_t >= PAPER_MIN_TRADES_FOR_PF else None
        wr  = round(state_fresh.get("total_wins", 0) / max(n_t, 1), 3) if n_t > 0 else None

        result.update({
            "total_trades": n_t,
            "pf_live":      pf,
            "wr_live":      wr,
            "max_dd_pct":   state_fresh.get("max_dd_pct", 0.0),
            "cumulative_pnl_pct": state_fresh.get("cumulative_pnl_pct", 0.0),
            "all_gates_ok": gates_ok,
            "verdict":      "LIVE_CANDIDATE" if gates_ok else "PAPER_ONLY",
            "start_date":   state_fresh.get("start_date"),
        })
        fleet_results.append(result)

        dt_ms = (time.time() - t_sym) * 1000
        action_disp = result.get("action", "?")
        p_disp = f"p={result['p_long']:.4f}" if result.get("p_long") is not None else ""
        print(f"   {sym:<12} {action_disp:<22} {p_disp:<14} [{dt_ms:.0f}ms]")

    # ── Sauvegarde fleet_summary ───────────────────────────────────────────────
    summary = {
        "timestamp":       now.isoformat(),
        "btc_regime":      btc_regime,
        "btc_price":       round(float(close_btc[-1]), 2),
        "btc_vs_ema200":   round(btc_vs_ema, 2),
        "val_stats":       val_stats,
        "assets":          fleet_results,
        "n_assets":        len(fleet_results),
        "n_long_signals":  sum(1 for r in fleet_results if r.get("action") == "LONG"),
        "n_watch":         sum(1 for r in fleet_results if r.get("action") == "WATCH"),
        "elapsed_s":       round(time.time() - t0, 1),
    }
    FLEET_SUMMARY.write_text(json.dumps(summary, indent=2, default=str))

    # ── Affichage récapitulatif ────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  BTC [{btc_regime}]  {close_btc[-1]:,.0f} USDT  EMA200={btc_vs_ema:+.1f}%")
    print(f"  LONG signals : {summary['n_long_signals']}  |  WATCH : {summary['n_watch']}")
    print(f"  Durée totale : {time.time()-t0:.1f}s  |  {len(fleet_results)} assets traités")
    if missing_parquet:
        print(f"  Parquets manquants : {missing_parquet}")
    print("=" * 68)
    print("  !! PAPER TRADING UNIQUEMENT — LIVE_ENABLED=False !!")
    print("=" * 68)


if __name__ == "__main__":
    main()
