#!/usr/bin/env python3
"""
run_simple_backtest.py — Phase 1 : Stratégie Minimale
======================================================

Stratégie :
  - Entrée LONG si prob_up > ENTRY_THRESHOLD ET volatilité suffisante (ATR% > Q25)
  - TP = TP_ATR_MULT × ATR(14) depuis le prix d'entrée
  - SL = SL_ATR_MULT × ATR(14) depuis le prix d'entrée
  - Horizon max : MAX_HOLD_BARS barres (60 min sur 1m)
  - Exécution : open[t+1] → zéro lookahead garanti

Fees + slippage (round-trip) :
  - Fee taker Binance : 4 bps/leg × 2 = 8 bps
  - Slippage estimé   : 2 bps/leg × 2 = 4 bps
  - Total RT          : 12 bps = 0.12%

Signal prob_up :
  - Mode "model"    : charge les prédictions depuis un fichier parquet
  - Mode "heuristic": EMA(8/21) cross + RV-normalisé (causal, sans lookahead)

Usage :
  # Heuristique (pas de modèle entraîné) :
  python run_simple_backtest.py --data /path/to/btcusd_bitstamp_1min_2012-2025.csv.gz

  # Avec prédictions du modèle :
  python run_simple_backtest.py --data /path/to/data.csv.gz --preds /path/to/preds.parquet

  # Paramètres personnalisés :
  python run_simple_backtest.py --data ... --start 2021-01-01 --end 2024-12-31 --equity 10000

Critères validation Phase 1 :
  ✓ Sharpe annualisé > 1.0
  ✓ Max Drawdown < 15%
  ✓ Profit Factor > 1.2
  ✓ Zéro lookahead (garanti structurellement)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

@dataclass
class BacktestConfig:
    # Stratégie
    entry_threshold: float = 0.55   # prob_up minimum pour entrer
    tp_atr_mult: float    = 1.5     # TP = 1.5 × ATR
    sl_atr_mult: float    = 1.0     # SL = 1.0 × ATR
    max_hold_bars: int    = 60      # Horizon max : 60 barres (= 60 min sur 1m)
    atr_period: int       = 14      # Période ATR
    min_atr_pct_q: float  = 0.25   # Filtre vol : ATR% doit être > Q25

    # Coûts (round-trip total en fraction)
    fee_rt_bps: float     = 8.0    # Fees taker (4 bps × 2 legs)
    slippage_rt_bps: float = 4.0   # Slippage (2 bps × 2 legs)

    # Sizing
    equity_init: float    = 10_000.0
    risk_pct: float       = 0.01    # 1% du capital risqué par trade (sur le SL)
    max_position_pct: float = 0.20  # Cap position à 20% du capital

    # Signal heuristique
    ema_fast: int = 8
    ema_slow: int = 21
    signal_lookback: int = 60       # Fenêtre retour pour normalisation signal

    # Données
    start_date: Optional[str] = None
    end_date: Optional[str]   = None
    symbol: str = "BTCUSDT"


# ─────────────────────────────────────────────
# CHARGEMENT DONNÉES
# ─────────────────────────────────────────────

def load_ohlcv(path: str, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    """Charge le CSV Bitstamp 1m (ou tout CSV OHLCV compatible)."""
    path = Path(path)
    print(f"[data] Chargement {path.name} …")
    t0 = time.time()

    df = pd.read_csv(path, dtype={
        "timestamp": "int64",
        "open": "float32",
        "high": "float32",
        "low": "float32",
        "close": "float32",
        "volume": "float32",
    })

    # Normalise les noms de colonnes
    df.columns = df.columns.str.lower()

    # Timestamp → datetime UTC
    if "timestamp" in df.columns:
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    elif "datetime" in df.columns:
        df["dt"] = pd.to_datetime(df["datetime"], utc=True)
    else:
        raise ValueError("Colonne timestamp ou datetime introuvable.")

    df = df.sort_values("dt").reset_index(drop=True)

    # Filtrage par dates
    if start:
        df = df[df["dt"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["dt"] <= pd.Timestamp(end, tz="UTC")]

    # Supprime les lignes sans volume (gaps marché)
    df = df[df["volume"] > 0].reset_index(drop=True)

    # Arrondi float32 → évite artefacts numériques
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype("float64")

    print(f"[data] {len(df):,} barres | {df['dt'].iloc[0].date()} → {df['dt'].iloc[-1].date()} ({time.time()-t0:.1f}s)")
    return df


# ─────────────────────────────────────────────
# FEATURES
# ─────────────────────────────────────────────

def compute_features(df: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """
    Calcule toutes les features de façon causale (aucun lookahead).
    Toutes les séries utilisent uniquement les données ≤ bar t.
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]

    # ── ATR(14) ──────────────────────────────────────────────────────────
    prev_c = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_c).abs(),
        (l - prev_c).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=cfg.atr_period, adjust=False).mean()
    df["atr"] = atr
    df["atr_pct"] = atr / c.clip(lower=1e-9)   # ATR en % du prix

    # ── EMAs ─────────────────────────────────────────────────────────────
    df["ema_fast"] = c.ewm(span=cfg.ema_fast, adjust=False).mean()
    df["ema_slow"] = c.ewm(span=cfg.ema_slow, adjust=False).mean()

    # ── Log-returns ───────────────────────────────────────────────────────
    log_ret = np.log(c / c.shift(1))
    df["log_ret"] = log_ret

    # ── Realized Volatility 30 barres ────────────────────────────────────
    df["rv30"] = log_ret.rolling(30, min_periods=10).std() * np.sqrt(30)

    # ── RSI(14) ───────────────────────────────────────────────────────────
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    # ── Momentum 60 barres ────────────────────────────────────────────────
    df["mom60"] = np.log(c / c.shift(60))

    # ── ATR% quantile (causal) ────────────────────────────────────────────
    # Filtre low-vol : ATR% doit être > Q25 sur la fenêtre glissante 500 barres
    df["atr_pct_q25"] = df["atr_pct"].rolling(500, min_periods=100).quantile(cfg.min_atr_pct_q)

    # ── Tendance confirmée sur 3 barres ───────────────────────────────────
    trend_up = (df["ema_fast"] > df["ema_slow"]).astype(int)
    df["trend_confirmed"] = trend_up.rolling(3, min_periods=3).min().astype(bool)

    # ── Volume relatif ────────────────────────────────────────────────────
    vol_ma = df["volume"].rolling(60, min_periods=20).mean().clip(lower=1e-9)
    df["vol_ratio"] = df["volume"] / vol_ma

    return df


# ─────────────────────────────────────────────
# SIGNAL HEURISTIQUE
# ─────────────────────────────────────────────

def compute_heuristic_signal(df: pd.DataFrame, cfg: BacktestConfig, freq_min: int = 1) -> pd.Series:
    """
    Signal "Channel Breakout" — pattern documenté pour fonctionner avec triple-barrier.

    Logique (entièrement causale) :
      1. Cassure haussière du canal N barres : close > max(high[i-N:i])
         → les breakouts de canal tendent à continuer dans la même direction
      2. Filtre tendance : dans une tendance haussière (close > EMA200)
      3. Volume confirmant le breakout
      4. Pas en zone de surachat extrême (RSI < 75)

    Paramètre freq_min : fréquence des barres (1=1m, 60=1h, 240=4h).

    Note : Ce signal est un PLACEHOLDER pour le vrai EdgeForecaster.
    Objectif : TP hit rate > 45% pour couvrir les coûts avec TP=1.5×ATR, SL=1×ATR.
    """
    c   = df["close"]
    h   = df["high"]
    rsi = df["rsi14"]
    vol = df["volume"]

    bars_per_hour = max(1, 60 // freq_min)
    bars_per_day  = bars_per_hour * 24

    # ── 1. Channel breakout (N barres) ───────────────────────────────────
    # Sur 1h bars : lookback=20 = 20 heures
    # Sur 4h bars : lookback=20 = 80 heures = ~3.3 jours
    lookback = 20
    channel_high = h.shift(1).rolling(lookback, min_periods=lookback // 2).max()
    breakout_up  = c > channel_high    # price breaks above recent high

    # ── 2. Tendance long terme ────────────────────────────────────────────
    ema_50  = c.ewm(span=50, adjust=False, min_periods=20).mean()
    ema_200 = c.ewm(span=200, adjust=False, min_periods=50).mean()
    in_uptrend  = (c > ema_50) & (ema_50 > ema_200)

    # ── 3. Volume confirmant le breakout ─────────────────────────────────
    vol_window = max(24, bars_per_day)
    vol_ma     = vol.rolling(vol_window, min_periods=vol_window // 4).mean().clip(lower=1e-9)
    vol_confirm = vol > 1.1 * vol_ma    # volume légèrement au-dessus de la moyenne

    # ── 4. RSI : pas en surachat extrême ─────────────────────────────────
    rsi_ok = rsi < 75

    # ── 5. Pas en explosion de volatilité (filtre panic) ─────────────────
    atr_pct_ma = df["atr_pct"].rolling(vol_window, min_periods=vol_window // 4).mean().clip(lower=1e-9)
    not_panic   = df["atr_pct"] < 4.0 * atr_pct_ma

    # ── Mapping → prob_up ────────────────────────────────────────────────
    # Signal de base : 0.30 (pas de signal)
    # Breakout + tendance : 0.62
    # Breakout + tendance + volume : 0.67
    # Tout aligné : 0.72
    prob_up = pd.Series(0.30, index=df.index)

    base_signal    = breakout_up & in_uptrend & rsi_ok & not_panic
    strong_signal  = base_signal & vol_confirm

    prob_up = prob_up.where(~base_signal,   0.62)
    prob_up = prob_up.where(~strong_signal, 0.67)

    # Bruit minimal pour éviter les blocs parfaits
    np.random.seed(42)
    noise   = pd.Series(np.random.uniform(-0.005, 0.005, len(df)), index=df.index)
    prob_up = (prob_up + noise).clip(0.20, 0.90)

    return prob_up


# ─────────────────────────────────────────────
# TRIPLE-BARRIER SIMULATION (vectorisée)
# ─────────────────────────────────────────────

def simulate_triple_barrier(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    cfg: BacktestConfig,
) -> pd.DataFrame:
    """
    Simule les sorties triple-barrier pour chaque entrée.

    GARANTIE ANTI-LOOKAHEAD :
      - entry_bar i → exécution à open[i+1]
      - ATR utilisé = atr[i] (pas de bar futur)
      - Sortie cherchée sur barres i+1 … i+MAX_HOLD

    GESTION DE POSITION : 1 seul trade actif à la fois.
    On ne peut pas entrer tant que le trade précédent n'est pas clôturé.

    Renvoie un DataFrame de trades.
    """
    opens  = df["open"].values
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    atrs   = df["atr"].values
    dts    = df["dt"].values

    n = len(df)
    rt_cost = (cfg.fee_rt_bps + cfg.slippage_rt_bps) / 10_000.0

    trades = []
    entry_indices = np.where(entry_mask.values)[0]

    # Prochain bar disponible + équité courante (updated après chaque trade)
    next_available = 0
    current_equity = cfg.equity_init

    for i in entry_indices:
        # GESTION DE POSITION : skip si on est encore dans un trade
        if i < next_available:
            continue
        # Vérifie qu'on peut exécuter à bar i+1
        if i + 1 >= n:
            break

        # Circuit breaker : stop si equity < 20% du capital initial
        if current_equity < cfg.equity_init * 0.20:
            break

        # ── Entrée ─────────────────────────────────────────────────────
        entry_px = opens[i + 1]          # open du bar suivant (pas de lookahead)
        atr_i    = atrs[i]               # ATR au bar signal (causal)

        if entry_px <= 0 or atr_i <= 0 or np.isnan(entry_px) or np.isnan(atr_i):
            continue

        tp_px = entry_px + cfg.tp_atr_mult * atr_i
        sl_px = entry_px - cfg.sl_atr_mult * atr_i

        # ── Sizing basé sur équité courante ────────────────────────────
        risk_budget = current_equity * cfg.risk_pct
        sl_dist     = entry_px - sl_px
        if sl_dist <= 0:
            continue
        qty = risk_budget / sl_dist
        # Cap position à max_position_pct de l'équité courante
        max_qty = (current_equity * cfg.max_position_pct) / entry_px
        qty = min(qty, max_qty)

        # ── Recherche sortie ────────────────────────────────────────────
        exit_px      = None
        exit_reason  = "time"
        exit_idx     = min(i + 1 + cfg.max_hold_bars, n - 1)

        for j in range(i + 1, exit_idx + 1):
            # SL prioritaire dans la même bougie (pire cas)
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
            # Sortie à la clôture du dernier bar (time stop)
            exit_px     = closes[exit_idx]
            exit_reason = "time"

        if exit_px is None or exit_px <= 0:
            continue

        # ── PnL ────────────────────────────────────────────────────────
        gross_pnl = (exit_px - entry_px) * qty
        cost      = entry_px * qty * rt_cost     # coût sur notionnel entrant
        net_pnl   = gross_pnl - cost

        hold_bars = exit_idx - (i + 1)

        # Mise à jour de l'équité courante et blocage jusqu'à sortie
        current_equity += net_pnl
        next_available  = exit_idx + 1

        trades.append({
            "t_entry"     : dts[i + 1],
            "t_exit"      : dts[exit_idx],
            "entry_px"    : entry_px,
            "exit_px"     : float(exit_px),
            "tp_px"       : tp_px,
            "sl_px"       : sl_px,
            "atr_i"       : atr_i,
            "qty"         : qty,
            "notional"    : entry_px * qty,
            "gross_pnl"   : gross_pnl,
            "cost"        : cost,
            "net_pnl"     : net_pnl,
            "exit_reason" : exit_reason,
            "hold_bars"   : hold_bars,
        })

    return pd.DataFrame(trades)


# ─────────────────────────────────────────────
# EQUITY CURVE
# ─────────────────────────────────────────────

def build_equity_curve(trades: pd.DataFrame, equity_init: float) -> pd.DataFrame:
    """Construit la courbe d'équité et le drawdown."""
    t = trades.sort_values("t_exit").copy()
    t["equity"] = equity_init + t["net_pnl"].cumsum()
    t["peak"]   = t["equity"].cummax()
    t["dd"]     = (t["equity"] - t["peak"]) / t["peak"]
    return t


# ─────────────────────────────────────────────
# MÉTRIQUES
# ─────────────────────────────────────────────

def compute_metrics(trades: pd.DataFrame, equity_init: float) -> dict:
    """Calcule toutes les métriques de backtest."""
    if trades.empty:
        return {"error": "aucun trade"}

    eq = build_equity_curve(trades, equity_init)
    pnl = trades["net_pnl"]
    wins   = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    # ── Equity & drawdown ─────────────────────────────────────────────
    final_equity = float(eq["equity"].iloc[-1])
    total_return = (final_equity - equity_init) / equity_init
    max_dd       = float(eq["dd"].min())

    # ── Durée ─────────────────────────────────────────────────────────
    duration_days = (
        pd.Timestamp(trades["t_exit"].max()) -
        pd.Timestamp(trades["t_entry"].min())
    ).total_seconds() / 86_400
    duration_days = max(duration_days, 1.0)

    # ── Retours journaliers pour Sharpe ───────────────────────────────
    eq_idx = eq.set_index("t_exit")["equity"]
    eq_idx.index = pd.to_datetime(eq_idx.index)
    daily_eq  = eq_idx.resample("1D").last().ffill()
    daily_ret = daily_eq.pct_change().dropna()
    mu_d      = float(daily_ret.mean())
    std_d     = float(daily_ret.std()) or 1e-9
    down_std  = float(daily_ret[daily_ret < 0].std()) or 1e-9

    sharpe  = (mu_d / std_d)  * np.sqrt(252)
    sortino = (mu_d / down_std) * np.sqrt(252)
    calmar  = (total_return * 365 / duration_days) / abs(max_dd) if max_dd < 0 else 0.0

    # ── Win/loss ──────────────────────────────────────────────────────
    n_trades     = len(trades)
    win_rate     = float(len(wins) / n_trades) if n_trades > 0 else 0.0
    avg_win      = float(wins.mean())  if len(wins)   > 0 else 0.0
    avg_loss     = float(losses.mean()) if len(losses) > 0 else 0.0
    profit_factor = abs(wins.sum() / losses.sum()) if losses.sum() < 0 else float("inf")

    # ── Exits ─────────────────────────────────────────────────────────
    exit_counts = trades["exit_reason"].value_counts().to_dict()

    # ── Coûts ─────────────────────────────────────────────────────────
    total_cost   = float(trades["cost"].sum())
    avg_cost_bps = (total_cost / trades["notional"].sum()) * 10_000 if trades["notional"].sum() > 0 else 0.0

    # ── Métriques par an ──────────────────────────────────────────────
    ann_return = total_return * 365 / duration_days

    return {
        # Portfolio
        "equity_init"    : equity_init,
        "equity_final"   : final_equity,
        "total_return_pct": round(total_return * 100, 2),
        "annual_return_pct": round(ann_return * 100, 2),
        "duration_days"  : round(duration_days, 1),

        # Risk
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe"          : round(sharpe, 3),
        "sortino"         : round(sortino, 3),
        "calmar"          : round(calmar, 3),

        # Trades
        "n_trades"       : n_trades,
        "win_rate_pct"   : round(win_rate * 100, 2),
        "profit_factor"  : round(profit_factor, 3),
        "avg_win"        : round(avg_win, 4),
        "avg_loss"       : round(avg_loss, 4),
        "avg_hold_bars"  : round(float(trades["hold_bars"].mean()), 1),
        "trades_per_day" : round(n_trades / duration_days, 2),

        # Exits
        "exit_tp_pct"    : round(exit_counts.get("tp", 0)   / n_trades * 100, 1),
        "exit_sl_pct"    : round(exit_counts.get("sl", 0)   / n_trades * 100, 1),
        "exit_time_pct"  : round(exit_counts.get("time", 0) / n_trades * 100, 1),

        # Coûts
        "total_cost"     : round(total_cost, 2),
        "avg_cost_bps"   : round(avg_cost_bps, 2),
    }


# ─────────────────────────────────────────────
# VALIDATION PHASE 1
# ─────────────────────────────────────────────

PHASE1_CRITERIA = {
    "sharpe"           : (">",  1.0),
    "max_drawdown_pct" : (">", -15.0),
    "profit_factor"    : (">",  1.2),
    "n_trades"         : (">",  50),
}

def validate_phase1(metrics: dict) -> Tuple[bool, list]:
    """Vérifie les critères de validation Phase 1."""
    results = []
    all_pass = True

    for key, (op, threshold) in PHASE1_CRITERIA.items():
        val = metrics.get(key, None)
        if val is None:
            results.append((key, False, "N/A", threshold, op))
            all_pass = False
            continue
        if op == ">":
            passed = val > threshold
        elif op == "<":
            passed = val < threshold
        else:
            passed = val == threshold
        results.append((key, passed, val, threshold, op))
        if not passed:
            all_pass = False

    return all_pass, results


# ─────────────────────────────────────────────
# PRINT RAPPORT
# ─────────────────────────────────────────────

def print_report(metrics: dict, validation: Tuple[bool, list], cfg: BacktestConfig, signal_mode: str) -> None:
    SEP = "=" * 65

    print(f"\n{SEP}")
    print("  BACKTEST PHASE 1 — STRATÉGIE MINIMALE")
    print(SEP)
    print(f"  Signal      : {signal_mode}")
    print(f"  TP          : {cfg.tp_atr_mult}× ATR(14)")
    print(f"  SL          : {cfg.sl_atr_mult}× ATR(14)")
    print(f"  Horizon max : {cfg.max_hold_bars} bars")
    print(f"  Seuil entrée: prob_up > {cfg.entry_threshold}")
    print(f"  Coûts RT    : {cfg.fee_rt_bps + cfg.slippage_rt_bps:.0f} bps")
    print(f"  Capital     : ${cfg.equity_init:,.0f}")
    print(f"  Risque/trade: {cfg.risk_pct*100:.1f}%")
    print(SEP)

    print("\n  ── RÉSULTATS ──────────────────────────────────────────")
    print(f"  Période           : {metrics['duration_days']:.0f} jours")
    print(f"  Nombre de trades  : {metrics['n_trades']:,}")
    print(f"  Trades / jour     : {metrics['trades_per_day']:.2f}")
    print(f"  Hold moyen        : {metrics['avg_hold_bars']:.1f} bars")
    print()
    print(f"  Capital initial   : ${metrics['equity_init']:>12,.2f}")
    print(f"  Capital final     : ${metrics['equity_final']:>12,.2f}")
    print(f"  Rendement total   : {metrics['total_return_pct']:>+8.2f}%")
    print(f"  Rendement annuel  : {metrics['annual_return_pct']:>+8.2f}%")
    print()
    print(f"  Sharpe            : {metrics['sharpe']:>8.3f}")
    print(f"  Sortino           : {metrics['sortino']:>8.3f}")
    print(f"  Calmar            : {metrics['calmar']:>8.3f}")
    print(f"  Max Drawdown      : {metrics['max_drawdown_pct']:>8.2f}%")
    print()
    print(f"  Win rate          : {metrics['win_rate_pct']:>8.2f}%")
    print(f"  Profit factor     : {metrics['profit_factor']:>8.3f}")
    print(f"  Avg gain          : ${metrics['avg_win']:>10.4f}")
    print(f"  Avg loss          : ${metrics['avg_loss']:>10.4f}")
    print()
    print(f"  Sorties TP        : {metrics['exit_tp_pct']:>6.1f}%")
    print(f"  Sorties SL        : {metrics['exit_sl_pct']:>6.1f}%")
    print(f"  Sorties Time      : {metrics['exit_time_pct']:>6.1f}%")
    print()
    print(f"  Coûts totaux      : ${metrics['total_cost']:>10.2f}")
    print(f"  Coût moyen        : {metrics['avg_cost_bps']:>6.2f} bps")

    print(f"\n  ── VALIDATION PHASE 1 ─────────────────────────────────")
    all_pass, results = validation
    for key, passed, val, threshold, op in results:
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {key:<22} : {str(val):>8}  (doit être {op} {threshold})")

    print()
    if all_pass:
        print("  ✅  PHASE 1 VALIDÉE — tous les critères satisfaits")
    else:
        n_fail = sum(1 for _, p, *_ in results if not p)
        print(f"  ❌  PHASE 1 NON VALIDÉE — {n_fail} critère(s) échoué(s)")
    print(SEP + "\n")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def resample_ohlcv(df: pd.DataFrame, freq_min: int) -> pd.DataFrame:
    """
    Resamples les données OHLCV 1m vers une fréquence plus large.

    Paramètre freq_min : fréquence en minutes (ex: 60 → 1h, 240 → 4h).
    Utilise UNIQUEMENT les barres complètes (completed_only = True) pour éviter
    tout lookahead sur la barre en cours.
    """
    if freq_min <= 1:
        return df
    print(f"[resample] {freq_min}m → agrégation OHLCV ({len(df):,} barres → …)")
    df2 = df.set_index("dt").sort_index()
    rule = f"{freq_min}min"
    agg  = df2.resample(rule, closed="left", label="left").agg({
        "open"  : "first",
        "high"  : "max",
        "low"   : "min",
        "close" : "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    # Ne garde que les barres complètes (au moins freq_min barres 1m dans la fenêtre)
    count = df2["close"].resample(rule, closed="left", label="left").count()
    agg   = agg[count >= freq_min * 0.90]   # tolère 10% de données manquantes
    agg   = agg.reset_index().rename(columns={"index": "dt", "dt": "dt"})
    if "dt" not in agg.columns and "level_0" in agg.columns:
        agg = agg.rename(columns={"level_0": "dt"})
    print(f"[resample] → {len(agg):,} barres {freq_min}m")
    return agg


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data",      required=True,   help="Chemin vers le CSV OHLCV (peut être .gz)")
    p.add_argument("--preds",     default=None,    help="Parquet avec colonne 'prob_up' et index datetime (optionnel)")
    p.add_argument("--start",     default="2020-01-01", help="Date début (YYYY-MM-DD)")
    p.add_argument("--end",       default=None,    help="Date fin (YYYY-MM-DD)")
    p.add_argument("--equity",    default=10000.0, type=float, help="Capital initial en USD")
    p.add_argument("--threshold", default=0.55,    type=float, help="Seuil prob_up pour entrée")
    p.add_argument("--tp",        default=1.5,     type=float, help="Multiplicateur TP (× ATR)")
    p.add_argument("--sl",        default=1.0,     type=float, help="Multiplicateur SL (× ATR)")
    p.add_argument("--max-hold",  default=60,      type=int,   help="Horizon max en barres")
    p.add_argument("--resample",  default=60,      type=int,   help="Resampling en minutes (1=1m, 60=1h, 240=4h). Défaut: 60 (1h)")
    p.add_argument("--out",       default=None,    help="Fichier JSON pour sauvegarder les métriques")
    return p.parse_args()


def main():
    args = parse_args()

    cfg = BacktestConfig(
        entry_threshold  = args.threshold,
        tp_atr_mult      = args.tp,
        sl_atr_mult      = args.sl,
        max_hold_bars    = args.max_hold,
        equity_init      = args.equity,
        start_date       = args.start,
        end_date         = args.end,
    )

    freq_min = args.resample

    # ── 1. Chargement des données ─────────────────────────────────────
    df = load_ohlcv(args.data, cfg.start_date, cfg.end_date)

    # ── 1b. Resampling vers la fréquence cible ────────────────────────
    if freq_min > 1:
        df = resample_ohlcv(df, freq_min)
        # Ajuste max_hold_bars proportionnellement si l'arg par défaut est utilisé
        # (60 barres 1m = 1h; sur 1h bars, 48 barres = 48h = 2 jours — donne plus
        #  de temps aux trades pour atteindre le TP avant expiration)
        if args.max_hold == 60:
            cfg.max_hold_bars = max(12, 48 * 60 // freq_min)   # ~48h en barres de freq_min
        print(f"[resample] max_hold_bars ajusté → {cfg.max_hold_bars} barres ({cfg.max_hold_bars * freq_min // 60}h)")

    if len(df) < 500:
        print(f"[ERREUR] Pas assez de données : {len(df)} barres (minimum 500)")
        sys.exit(1)

    # ── 2. Calcul des features ────────────────────────────────────────
    print("[features] Calcul des indicateurs …")
    df = compute_features(df, cfg)

    # ── 3. Signal prob_up ─────────────────────────────────────────────
    if args.preds is not None:
        print(f"[signal] Chargement prédictions depuis {args.preds} …")
        preds = pd.read_parquet(args.preds)
        if "prob_up" not in preds.columns:
            raise ValueError("Le fichier preds doit contenir une colonne 'prob_up'")
        preds.index = pd.to_datetime(preds.index, utc=True)
        df = df.set_index("dt")
        df["prob_up"] = preds["prob_up"].reindex(df.index, method="ffill")
        df = df.reset_index().rename(columns={"index": "dt"})
        signal_mode = f"EdgeForecaster ({Path(args.preds).name})"
    else:
        print("[signal] Calcul signal heuristique (EMA cross + momentum) …")
        df["prob_up"] = compute_heuristic_signal(df, cfg, freq_min=freq_min)
        signal_mode = f"Heuristique EMA (barres {freq_min}m)"

    # ── 4. Filtre warmup : ignore les N premières barres ──────────────
    # Sur 1m : ~500 barres ; sur 1h : ~200 barres (≈ 8 jours)
    min_warmup = max(200, 500 // max(1, freq_min))
    warmup = max(cfg.atr_period * 3, cfg.signal_lookback * 2, min_warmup)
    df_backtest = df.iloc[warmup:].reset_index(drop=True)
    print(f"[signal] Warmup : {warmup} barres ignorées | {len(df_backtest):,} barres pour le backtest")

    # ── 5. Masque d'entrée (CAUSAL) ───────────────────────────────────
    #   Signal calculé à bar t → exécution à open[t+1]
    #   Filtres :
    #     a) prob_up > threshold
    #     b) ATR% > quantile 25 (filtre faible volatilité — évite les marchés plats)
    #     c) Volume ≥ 0.5× moyenne (filtre périodes illiquides)
    #     d) Pas de NaN sur les features critiques
    prob_ok  = df_backtest["prob_up"] > cfg.entry_threshold
    vol_ok   = df_backtest["atr_pct"] > df_backtest["atr_pct_q25"].fillna(0)
    liq_ok   = df_backtest["vol_ratio"].fillna(0) >= 0.5
    no_nan   = (
        df_backtest["prob_up"].notna() &
        df_backtest["atr"].notna() &
        df_backtest["atr_pct_q25"].notna()
    )
    entry_mask = prob_ok & vol_ok & liq_ok & no_nan

    n_signals = int(entry_mask.sum())
    print(f"[signal] {n_signals:,} signaux ({n_signals/len(df_backtest)*100:.1f}% des barres)")

    if n_signals == 0:
        print("[ERREUR] Aucun signal généré. Vérifiez le threshold ou les données.")
        sys.exit(1)

    # ── 6. Simulation triple-barrier ──────────────────────────────────
    print("[backtest] Simulation triple-barrier …")
    t_sim = time.time()
    trades = simulate_triple_barrier(df_backtest, entry_mask, cfg)
    print(f"[backtest] {len(trades):,} trades simulés en {time.time()-t_sim:.1f}s")

    if trades.empty:
        print("[ERREUR] Aucun trade simulé.")
        sys.exit(1)

    # ── 7. Métriques ──────────────────────────────────────────────────
    metrics = compute_metrics(trades, cfg.equity_init)

    # ── 8. Validation Phase 1 ─────────────────────────────────────────
    validation = validate_phase1(metrics)

    # ── 9. Rapport ────────────────────────────────────────────────────
    print_report(metrics, validation, cfg, signal_mode)

    # ── 10. Sauvegarde optionnelle ────────────────────────────────────
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(metrics, indent=2, default=str))
        print(f"[out] Métriques sauvegardées → {out_path}")

    # Exit code : 0 si validé, 1 sinon
    sys.exit(0 if validation[0] else 1)


if __name__ == "__main__":
    main()
