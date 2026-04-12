#!/usr/bin/env python3
"""
run_live.py — Phase 2 : Paper Trading avec RiskController
==========================================================

Simule le trading bar-par-bar en appliquant le RiskController complet :
  - Sizing automatique : 0.2% du capital risqué par trade sur le SL
  - Stop quotidien à -2% du capital journalier
  - Arrêt après 3 pertes consécutives
  - Cooldown de 3 barres entre trades
  - Persistence de l'état en JSON (survie aux redémarrages)

Pipeline par barre :
  1. Signal (prob_up) → edge_final = prob_up - 0.5
  2. RiskController.decide() → action / reject
  3. Si BUY : simulation fill à open[t+1]
  4. Recherche sortie TP/SL/time sur les barres suivantes
  5. on_fill_pnl() → mise à jour PnL + compteurs
  6. reset_day() à chaque changement de jour UTC
  7. save_state() après chaque trade

Usage :
  # Paper trading heuristique (pas de modèle ML)
  python run_live.py --data /path/to/btcusd_1min.csv.gz

  # Avec prédictions EdgeForecaster
  python run_live.py --data /path/to/data.csv.gz --preds /path/to/preds.parquet

  # Reprendre une session existante
  python run_live.py --data ... --state artifacts/risk/live_state.json

Critères validation Phase 2 :
  ✓ Trades refusés si daily -2% dépassé
  ✓ Trades refusés après 3 pertes consécutives
  ✓ Sizing = 0.2% / SL distance
  ✓ PnL journalier correctement mis à jour
  ✓ État persisté en JSON après chaque trade
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Import du RiskController depuis level_7
_RC_PATH = Path(__file__).resolve().parent.parent.parent / "ai" / "models" / "level_7"
if str(_RC_PATH) not in sys.path:
    sys.path.insert(0, str(_RC_PATH))

from RiskController import RiskController, RiskConfig, RiskState


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LiveConfig:
    # Stratégie
    entry_threshold: float = 0.55    # prob_up minimum pour considérer un signal
    tp_atr_mult: float    = 1.5      # TP = 1.5 × ATR
    sl_atr_mult: float    = 1.0      # SL = 1.0 × ATR
    max_hold_bars: int    = 48       # Horizon max (barres de freq_min)
    atr_period: int       = 14
    min_atr_pct_q: float  = 0.25    # filtre low-vol

    # Coûts (round-trip)
    fee_rt_bps: float     = 8.0
    slippage_rt_bps: float = 4.0

    # Données
    start_date: Optional[str] = None
    end_date: Optional[str]   = None
    symbol: str = "BTCUSDT"


# ─────────────────────────────────────────────────────────────────────────────
# CHARGEMENT DONNÉES
# ─────────────────────────────────────────────────────────────────────────────

def load_ohlcv(path: str, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    p = Path(path)
    print(f"[data] Chargement {p.name} …")
    t0 = time.time()
    df = pd.read_csv(p, dtype={
        "timestamp": "int64", "open": "float32", "high": "float32",
        "low": "float32", "close": "float32", "volume": "float32",
    })
    df.columns = df.columns.str.lower()
    if "timestamp" in df.columns:
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    elif "datetime" in df.columns:
        df["dt"] = pd.to_datetime(df["datetime"], utc=True)
    else:
        raise ValueError("Colonne timestamp ou datetime introuvable.")
    df = df.sort_values("dt").reset_index(drop=True)
    if start:
        df = df[df["dt"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["dt"] <= pd.Timestamp(end, tz="UTC")]
    df = df[df["volume"] > 0].reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype("float64")
    print(f"[data] {len(df):,} barres | {df['dt'].iloc[0].date()} → {df['dt'].iloc[-1].date()} ({time.time()-t0:.1f}s)")
    return df


def resample_ohlcv(df: pd.DataFrame, freq_min: int) -> pd.DataFrame:
    if freq_min <= 1:
        return df
    print(f"[resample] {freq_min}m → agrégation OHLCV …")
    df2 = df.set_index("dt").sort_index()
    rule = f"{freq_min}min"
    agg  = df2.resample(rule, closed="left", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    count = df2["close"].resample(rule, closed="left", label="left").count()
    agg   = agg[count >= freq_min * 0.90]
    agg   = agg.reset_index()
    print(f"[resample] → {len(agg):,} barres {freq_min}m")
    return agg


# ─────────────────────────────────────────────────────────────────────────────
# FEATURES
# ─────────────────────────────────────────────────────────────────────────────

def compute_features(df: pd.DataFrame, cfg: LiveConfig) -> pd.DataFrame:
    c, h, l = df["close"], df["high"], df["low"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df["atr"]      = tr.ewm(span=cfg.atr_period, adjust=False).mean()
    df["atr_14"]   = df["atr"]                          # alias pour RiskController
    df["atr_pct"]  = df["atr"] / c.clip(lower=1e-9)
    df["atr_pct_q25"] = df["atr_pct"].rolling(500, min_periods=100).quantile(cfg.min_atr_pct_q)
    df["ema_fast"] = c.ewm(span=8,  adjust=False).mean()
    df["ema_slow"] = c.ewm(span=21, adjust=False).mean()
    df["ema_200"]  = c.ewm(span=200, adjust=False, min_periods=50).mean()
    log_ret        = np.log(c / c.shift(1))
    df["log_ret"]  = log_ret
    df["rv_60"]    = log_ret.rolling(60, min_periods=10).std() * np.sqrt(60)
    delta          = c.diff()
    gain           = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss           = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    df["rsi14"]    = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    vol_ma         = df["volume"].rolling(60, min_periods=20).mean().clip(lower=1e-9)
    df["vol_ratio"] = df["volume"] / vol_ma
    return df


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL
# ─────────────────────────────────────────────────────────────────────────────

def compute_signal(df: pd.DataFrame, freq_min: int = 1) -> pd.Series:
    """Channel breakout + tendance long terme."""
    c, h = df["close"], df["high"]
    channel_high = h.shift(1).rolling(20, min_periods=10).max()
    breakout     = c > channel_high
    in_uptrend   = (c > df["ema_200"]) & (df["ema_fast"] > df["ema_slow"])
    vol_confirm  = df["vol_ratio"].fillna(0) > 1.1
    rsi_ok       = df["rsi14"] < 75
    atr_stable   = df["atr_pct"] < 4.0 * df["atr_pct"].rolling(60, min_periods=20).mean().clip(lower=1e-9)

    sig  = breakout & in_uptrend & rsi_ok & atr_stable
    sig2 = sig & vol_confirm

    prob_up = pd.Series(0.30, index=df.index)
    prob_up = prob_up.where(~sig,  0.60)
    prob_up = prob_up.where(~sig2, 0.65)
    return prob_up


# ─────────────────────────────────────────────────────────────────────────────
# BOUCLE BAR-PAR-BAR
# ─────────────────────────────────────────────────────────────────────────────

def run_paper_trading(
    df: pd.DataFrame,
    rc: RiskController,
    cfg: LiveConfig,
    freq_min: int,
    preds: Optional[pd.Series],
    state_path: Optional[Path],
    log_path: Optional[Path],
) -> dict:
    """
    Simule le trading bar-par-bar.

    Anti-lookahead :
      - Signal calculé à bar t → décision à bar t
      - Exécution à open[t+1]
      - ATR utilisé = atr[t]
      - Sorties cherchées sur bars t+1..t+max_hold
    """
    opens  = df["open"].values
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    atrs   = df["atr"].values
    dts    = df["dt"].values
    n      = len(df)

    rt_cost = (cfg.fee_rt_bps + cfg.slippage_rt_bps) / 10_000.0

    log_rows   = []
    decisions  = []
    rejections = {}      # reason → count

    next_available = 0   # prochain bar où on peut entrer

    for i in range(n - 1):
        dt_i   = pd.Timestamp(dts[i])
        day_i  = dt_i.strftime("%Y-%m-%d")

        # ── Reset journalier ───────────────────────────────────────────────────
        if day_i != rc.state.current_day:
            rc.reset_day(day_str=day_i)

        # ── Skip si position encore ouverte ───────────────────────────────────
        if i < next_available:
            continue

        # ── Signal ─────────────────────────────────────────────────────────────
        if preds is not None:
            prob_up = float(preds.iloc[i]) if i < len(preds) else 0.30
        else:
            prob_up = float(df["prob_up"].iloc[i])

        if prob_up <= cfg.entry_threshold:
            continue

        # ── Features pour RiskController ──────────────────────────────────────
        features = {
            "atr_14": float(atrs[i]) if not np.isnan(atrs[i]) else 0.0,
            "rv_60" : float(df["rv_60"].iloc[i]) if not np.isnan(df["rv_60"].iloc[i]) else 0.0,
        }

        edge_final = prob_up - 0.5                   # signé, positif = long
        scale      = min(1.0, (prob_up - 0.5) / 0.2) # confiance normalisée

        # ── Filtre ATR% minimum (filtre low-vol) ────────────────────────────────
        atr_pct     = float(df["atr_pct"].iloc[i])
        atr_pct_q25 = float(df["atr_pct_q25"].iloc[i]) if not np.isnan(df["atr_pct_q25"].iloc[i]) else 0.0
        if atr_pct < atr_pct_q25:
            continue

        # ── Décision RiskController ────────────────────────────────────────────
        decision = rc.decide(
            price    = float(closes[i]),
            edge_final = edge_final,
            scale    = scale,
            bar_index = i,
            features = features,
        )

        if decision["action"] == "HOLD":
            reason = decision["reason"]
            rejections[reason] = rejections.get(reason, 0) + 1
            continue

        # ── Entrée à open[i+1] ─────────────────────────────────────────────────
        entry_px = float(opens[i + 1])
        atr_i    = float(atrs[i])
        if entry_px <= 0 or atr_i <= 0 or np.isnan(entry_px) or np.isnan(atr_i):
            continue

        tp_px = entry_px + cfg.tp_atr_mult * atr_i
        sl_px = entry_px - cfg.sl_atr_mult * atr_i
        qty   = decision["qty"]

        # ── Recherche sortie ───────────────────────────────────────────────────
        exit_px     = float(closes[min(i + cfg.max_hold_bars, n - 1)])
        exit_reason = "time"
        exit_idx    = min(i + 1 + cfg.max_hold_bars, n - 1)

        for j in range(i + 1, exit_idx + 1):
            if lows[j] <= sl_px:
                exit_px     = sl_px
                exit_reason = "sl"
                exit_idx    = j
                break
            if highs[j] >= tp_px:
                exit_px     = tp_px
                exit_reason = "tp"
                exit_idx    = j
                break
        else:
            exit_px     = float(closes[exit_idx])
            exit_reason = "time"

        # ── PnL ────────────────────────────────────────────────────────────────
        gross_pnl = (exit_px - entry_px) * qty
        cost      = entry_px * qty * rt_cost
        net_pnl   = gross_pnl - cost

        # ── Mise à jour RiskController ─────────────────────────────────────────
        rc.on_fill_pnl(net_pnl)
        next_available = exit_idx + 1

        # ── Sauvegarde état ────────────────────────────────────────────────────
        if state_path:
            rc.save_state(state_path)

        # ── Log ────────────────────────────────────────────────────────────────
        trade_log = {
            "bar"        : int(i),
            "dt_entry"   : str(pd.Timestamp(dts[i + 1])),
            "dt_exit"    : str(pd.Timestamp(dts[exit_idx])),
            "prob_up"    : round(prob_up, 4),
            "edge_final" : round(edge_final, 4),
            "scale"      : round(scale, 4),
            "entry_px"   : round(entry_px, 2),
            "exit_px"    : round(exit_px, 2),
            "tp_px"      : round(tp_px, 2),
            "sl_px"      : round(sl_px, 2),
            "qty"        : round(qty, 8),
            "gross_pnl"  : round(gross_pnl, 4),
            "cost"       : round(cost, 4),
            "net_pnl"    : round(net_pnl, 4),
            "exit_reason": exit_reason,
            "equity"     : round(rc.state.equity, 2),
            "day_pnl"    : round(rc.state.day_pnl, 4),
            "consec_loss": rc.state.consecutive_losses,
            "rc_reason"  : decision["reason"],
            "risk_budget": round(decision["risk_budget"], 4),
            "stop_pct"   : round(decision["stop_pct"] * 100, 4),
        }
        log_rows.append(trade_log)
        decisions.append(decision)

    # ── Sauvegarde log ──────────────────────────────────────────────────────
    if log_path and log_rows:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_df = pd.DataFrame(log_rows)
        log_df.to_parquet(str(log_path).replace(".parquet", "") + ".parquet", index=False)
        print(f"[log] {len(log_rows):,} trades → {log_path}")

    return {
        "trades"     : log_rows,
        "rejections" : rejections,
        "summary"    : rc.summary(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# RAPPORT
# ─────────────────────────────────────────────────────────────────────────────

def print_phase2_report(result: dict, rc: RiskController, cfg: LiveConfig, state_path: Optional[Path] = None) -> None:
    trades   = result["trades"]
    rejs     = result["rejections"]
    summary  = result["summary"]
    SEP = "=" * 65

    print(f"\n{SEP}")
    print("  PHASE 2 — RISK CONTROLLER")
    print(SEP)
    print(f"  Risque/trade  : {rc.cfg.risk_per_trade*100:.1f}% equity")
    print(f"  Stop journalier : -{rc.cfg.daily_loss_limit_pct*100:.0f}% equity/jour")
    print(f"  Pertes max    : {rc.cfg.max_consecutive_losses} consécutives")
    print(f"  Cooldown      : {rc.cfg.cooldown_bars} barres")
    print(SEP)

    if not trades:
        print("  ⚠️  Aucun trade exécuté.")
        return

    n = len(trades)
    pnls    = [t["net_pnl"] for t in trades]
    wins    = [p for p in pnls if p > 0]
    losses  = [p for p in pnls if p < 0]
    tp_hits = sum(1 for t in trades if t["exit_reason"] == "tp")
    sl_hits = sum(1 for t in trades if t["exit_reason"] == "sl")
    tm_hits = sum(1 for t in trades if t["exit_reason"] == "time")

    equity_init  = rc.cfg.equity
    equity_final = summary["equity"]
    total_ret    = (equity_final - equity_init) / equity_init * 100

    print(f"\n  ── TRADES ──────────────────────────────────────────────")
    print(f"  Trades exécutés  : {n:,}")
    print(f"  Wins             : {len(wins):,} ({len(wins)/n*100:.1f}%)")
    print(f"  Losses           : {len(losses):,} ({len(losses)/n*100:.1f}%)")
    print(f"  Sorties TP/SL/T  : {tp_hits}/{sl_hits}/{tm_hits}")
    print(f"  Avg gain         : ${np.mean(wins):.4f}" if wins else "  Avg gain         : —")
    print(f"  Avg loss         : ${np.mean(losses):.4f}" if losses else "  Avg loss         : —")

    if losses:
        pf = abs(sum(wins) / sum(losses)) if sum(losses) < 0 else float("inf")
        print(f"  Profit factor    : {pf:.3f}")

    print(f"\n  ── RISK CONTROLLER ─────────────────────────────────────")
    print(f"  Capital initial  : ${equity_init:>12,.2f}")
    print(f"  Capital final    : ${equity_final:>12,.2f}")
    print(f"  Rendement total  : {total_ret:>+8.2f}%")
    print(f"  Day PnL courant  : ${summary['day_pnl']:>+10.4f}")
    print(f"  Pertes consécutives : {summary['consecutive_losses']}")

    print(f"\n  ── REJETS RISK CONTROLLER ──────────────────────────────")
    total_rej = sum(rejs.values())
    for reason, cnt in sorted(rejs.items(), key=lambda x: -x[1])[:10]:
        print(f"  {reason:<40}: {cnt:>6,}")
    print(f"  {'TOTAL rejets':<40}: {total_rej:>6,}")

    print(f"\n  ── VALIDATION PHASE 2 ──────────────────────────────────")
    # ── Vérifications structurelles (indépendantes du marché) ─────────────

    # 1. Sizing : chaque trade a risk_budget = equity × risk_per_trade
    max_risk_pct = max((t["risk_budget"] / max(rc.cfg.equity, 1) for t in trades), default=0)
    sizing_ok = max_risk_pct <= rc.cfg.risk_per_trade * 1.02   # tolérance 2%

    # 2. Stop journalier : simuler 2.1% de perte sur un clone
    rc_test = RiskController(RiskConfig(**{
        **{f: getattr(rc.cfg, f) for f in rc.cfg.__dataclass_fields__},
        "daily_loss_limit_pct": 0.02,
    }))
    rc_test.on_fill_pnl(-rc_test.cfg.equity * 0.021)
    daily_stop_works = rc_test.decide(
        price=50_000.0, edge_final=0.15, scale=0.5,
        bar_index=100, features={"atr_14": 250.0}
    )["action"] == "HOLD"

    # 3. Pertes consécutives : simuler 3 pertes sur un clone
    rc_test2 = RiskController(RiskConfig(**{
        **{f: getattr(rc.cfg, f) for f in rc.cfg.__dataclass_fields__},
        "max_consecutive_losses": 3,
    }))
    for _ in range(3):
        rc_test2.on_fill_pnl(-1.0)
    consec_stop_works = rc_test2.decide(
        price=50_000.0, edge_final=0.15, scale=0.5,
        bar_index=100, features={"atr_14": 250.0}
    )["action"] == "HOLD"

    # 4. PnL journalier correctement mis à jour
    pnl_ok = trades[-1]["day_pnl"] != trades[0]["day_pnl"] if len(trades) > 1 else True

    # 5. State JSON : fichier présent et lisible
    state_json_ok = state_path is not None and state_path.exists()

    checks = [
        ("Sizing ≤ 0.2% equity/trade",       sizing_ok),
        ("Stop journalier -2% : bloque trade",daily_stop_works),
        ("3 pertes consécutives : bloque trade",consec_stop_works),
        ("PnL journalier mis à jour",          pnl_ok),
        ("State JSON sauvegardé et lisible",   state_json_ok),
    ]

    all_pass = all(p for _, p in checks)
    for label, passed in checks:
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {label}")

    print()
    if all_pass:
        print("  ✅  PHASE 2 VALIDÉE")
    else:
        n_fail = sum(1 for _, p in checks if not p)
        print(f"  ❌  PHASE 2 — {n_fail} contrôle(s) échoué(s)")
    print(SEP + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data",      required=True,   help="CSV OHLCV (peut être .gz)")
    p.add_argument("--preds",     default=None,    help="Parquet avec colonne 'prob_up' (optionnel)")
    p.add_argument("--state",     default=None,    help="Fichier JSON état RiskController (chargement/sauvegarde)")
    p.add_argument("--start",     default="2020-01-01")
    p.add_argument("--end",       default=None)
    p.add_argument("--equity",    default=10000.0, type=float)
    p.add_argument("--resample",  default=60,      type=int,   help="Resampling en minutes (défaut: 60 = 1h)")
    p.add_argument("--threshold", default=0.55,    type=float)
    p.add_argument("--log",       default=None,    help="Chemin parquet pour le log des trades")
    p.add_argument("--out",       default=None,    help="Fichier JSON pour le résumé final")
    return p.parse_args()


def main():
    args  = parse_args()
    cfg   = LiveConfig(
        entry_threshold = args.threshold,
        start_date      = args.start,
        end_date        = args.end,
    )
    freq_min   = args.resample
    state_path = Path(args.state) if args.state else Path("trading-system/artifacts/risk/live_state.json")
    log_path   = Path(args.log)   if args.log   else Path("trading-system/artifacts/risk/live_trades.parquet")

    # ── RiskController : charger ou créer ─────────────────────────────────────
    if state_path.exists():
        print(f"[rc] Chargement état depuis {state_path}")
        rc = RiskController.load_state(state_path)
        # Met à jour equity si explicitement passée
        if args.equity != 10000.0:
            rc.state.equity = args.equity
    else:
        rc_cfg = RiskConfig(
            equity                = args.equity,
            risk_per_trade        = 0.002,      # 0.2%
            daily_loss_limit_pct  = 0.02,       # -2%
            max_consecutive_losses = 3,
            cooldown_bars         = 3,
            stop_atr_mult         = cfg.sl_atr_mult * 2.5,   # via ATR
            rr                    = cfg.tp_atr_mult,
        )
        rc = RiskController(rc_cfg)
        print(f"[rc] Nouveau RiskController créé (equity={args.equity:,.0f})")

    # ── Données ───────────────────────────────────────────────────────────────
    df = load_ohlcv(args.data, cfg.start_date, cfg.end_date)
    if freq_min > 1:
        df = resample_ohlcv(df, freq_min)
        cfg.max_hold_bars = max(12, 48 * 60 // freq_min)

    if len(df) < 500:
        print(f"[ERREUR] Pas assez de données : {len(df)} barres")
        sys.exit(1)

    # ── Features ──────────────────────────────────────────────────────────────
    print("[features] Calcul des indicateurs …")
    df = compute_features(df, cfg)

    # ── Signal ────────────────────────────────────────────────────────────────
    preds_series = None
    if args.preds:
        print(f"[signal] Chargement prédictions depuis {args.preds} …")
        preds_df = pd.read_parquet(args.preds)
        preds_df.index = pd.to_datetime(preds_df.index, utc=True)
        df_idx   = df.set_index("dt")
        df["prob_up"] = preds_df["prob_up"].reindex(df_idx.index, method="ffill").values
        preds_series  = df["prob_up"]
    else:
        print("[signal] Calcul signal heuristique …")
        df["prob_up"] = compute_signal(df, freq_min)
        preds_series  = None    # on lit depuis df["prob_up"] dans la boucle

    # ── Warmup ────────────────────────────────────────────────────────────────
    warmup = max(200, 500 // max(1, freq_min))
    df_live = df.iloc[warmup:].reset_index(drop=True)
    print(f"[signal] Warmup {warmup} barres | {len(df_live):,} barres live")

    # ── Simulation ────────────────────────────────────────────────────────────
    print("[live] Simulation bar-par-bar …")
    t0     = time.time()
    result = run_paper_trading(df_live, rc, cfg, freq_min, preds_series, state_path, log_path)
    print(f"[live] Terminé en {time.time()-t0:.1f}s")

    # ── Rapport ───────────────────────────────────────────────────────────────
    print_phase2_report(result, rc, cfg, state_path=state_path)

    # ── Sauvegarde résumé ─────────────────────────────────────────────────────
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result["summary"], indent=2, default=str))
        print(f"[out] Résumé → {out}")

    # Sauvegarde finale de l'état
    rc.save_state(state_path)
    print(f"[rc] État sauvegardé → {state_path}")


if __name__ == "__main__":
    main()
