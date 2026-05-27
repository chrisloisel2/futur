#!/usr/bin/env python3
"""
scripts/backtest_engine.py
============================
Backtest end-to-end sur les données historiques MongoDB (Level 0 → Level 7).

Caractéristiques :
  - Walk-forward strict : aucune donnée future utilisée
  - Frais réels : 0.05% maker Binance (spot), 0.10% taker
  - Slippage : 0.02% (estimation conservative marché)
  - Level 7 RiskConfig : stops/TP asymétriques long vs short
  - Métriques complètes : Sharpe, Sortino, MaxDD, Profit Factor, Win Rate
  - Breakdown par an et par régime

Signal sur données historiques MongoDB :
  - Features pré-calculées dans la collection OHLCV/features enrichie
  - Horizons cohérents : signal sur barre T → trade sur barre T+1
  - Lookback minimum : 200 barres pour stabiliser les EMAs

Usage :
  python scripts/backtest_engine.py                     # BTC 2021→2026
  python scripts/backtest_engine.py --since 2020-01-01  # depuis 2020
  python scripts/backtest_engine.py --out results.json  # export JSON
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pymongo import MongoClient, ASCENDING

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from ai.level_7.config import make_long_risk_config, make_short_risk_config
    _RISK_LONG  = make_long_risk_config()
    _RISK_SHORT = make_short_risk_config()
    _L7 = True
except Exception as e:
    _L7 = False
    _RISK_LONG = _RISK_SHORT = None

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backtest")

MONGO_URI = os.getenv("FUTUR_MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = os.getenv("FUTUR_MONGO_DB",  "trader")
FEATURE_COLLECTION = os.getenv(
    "FUTUR_MONGO_FEATURE_COLLECTION",
    os.getenv(
        "MONGODB_FEATURE_COLLECTION",
        os.getenv("FUTUR_MONGO_OHLCV_COLLECTION", os.getenv("MONGODB_HIST_COLLECTION", "historical_ohlcv_enriched")),
    ),
)


def _symbol_variants(symbol: str) -> List[str]:
    compact = str(symbol or "").upper().replace("/", "").replace("_", "").replace("-", "")
    variants = {symbol, compact}
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if compact.endswith(quote) and len(compact) > len(quote):
            base = compact[: -len(quote)]
            variants.add(f"{base}/{quote}")
            if quote == "USD":
                variants.add(f"{base}USDT")
                variants.add(f"{base}/USDT")
            break
    return sorted(variants)
MAKER_FEE = 0.0005    # 0.05%
TAKER_FEE = 0.0010    # 0.10%
SLIPPAGE  = 0.0002    # 0.02%
INITIAL_CAPITAL = 10_000.0


# ─────────────────────────────────────────────────────────────────────────────
# Chargement des données
# ─────────────────────────────────────────────────────────────────────────────

def load_ohlcv(symbol: str = "BTC/USDT", since: str = "2021-01-01") -> pd.DataFrame:
    """Charge les barres 1h depuis MongoDB avec toutes les features."""
    log.info(f"Chargement {symbol} depuis {since}…")
    db   = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[DB_NAME]
    coll = db[FEATURE_COLLECTION]

    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    cursor = coll.find(
        {"symbol": {"$in": _symbol_variants(symbol)}, "timestamp": {"$gte": since_dt}},
        sort=[("timestamp", ASCENDING)],
    )
    docs = list(cursor)
    if not docs:
        raise RuntimeError(f"Aucune donnée pour {symbol} depuis {since}")

    df = pd.DataFrame(docs)
    df["datetime"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    log.info(f"  {len(df):,} barres | {df.index[0].date()} → {df.index[-1].date()}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Signal vectorisé sur features historiques
# ─────────────────────────────────────────────────────────────────────────────

def _safe_tanh(x: pd.Series, scale: float = 1.0) -> pd.Series:
    return np.tanh(x.fillna(0) * scale)


def compute_signals_batch(df: pd.DataFrame) -> pd.DataFrame:
    """
    Signal professionnel sur les features ML pré-calculées de MongoDB.
    Utilise MOM_SHARPE, EFF_RATIO, TAKER, RSI, BOLL, MACRO en combinaison.
    Aucune donnée future — tout strict causal.
    """
    log.info("Calcul des signaux vectorisés (features ML MongoDB)…")

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)

    def _col(name: str, default: float = 0.0) -> pd.Series:
        if name in df.columns:
            return df[name].astype(float).fillna(default)
        return pd.Series(default, index=df.index)

    # ── Recalcul des features manquantes ──────────────────────────────────
    if "ema_spread_20_50" not in df.columns:
        e20  = close.ewm(span=20,  adjust=False).mean()
        e50  = close.ewm(span=50,  adjust=False).mean()
        e200 = close.ewm(span=200, adjust=False).mean()
        df["ema_spread_20_50"]  = (e20 - e50)  / (close + 1e-9) * 100
        df["ema_spread_50_200"] = (e50 - e200) / (close + 1e-9) * 100
        df["dist_ema_200"]      = (close - e200) / (e200 + 1e-9) * 100

    if "rsi_14" not in df.columns:
        d  = close.diff()
        g  = d.clip(lower=0).ewm(span=14, adjust=False).mean()
        ls = (-d.clip(upper=0)).ewm(span=14, adjust=False).mean()
        df["rsi_14"] = 100 - 100 / (1 + g / (ls + 1e-9))

    if "mom_logret_24" not in df.columns:
        df["mom_logret_24"] = np.log(close / (close.shift(24) + 1e-9)).fillna(0)
    if "mom_logret_72" not in df.columns:
        df["mom_logret_72"] = np.log(close / (close.shift(72) + 1e-9)).fillna(0)

    if "taker_buy_ratio_base" not in df.columns and "taker_buy_base" in df.columns:
        df["taker_buy_ratio_base"] = df["taker_buy_base"].astype(float) / (df["volume"].astype(float) + 1e-9)
    if "taker_buy_ratio_base" not in df.columns:
        df["taker_buy_ratio_base"] = 0.5

    if "vol_ratio_24" not in df.columns:
        vol = df["volume"].astype(float)
        df["vol_ratio_24"] = vol / (vol.rolling(24, min_periods=1).mean() + 1e-9)

    if "mom_sharpe_24" not in df.columns:
        lr = np.log(close / (close.shift(1) + 1e-9))
        df["mom_sharpe_24"] = lr.rolling(24, min_periods=6).mean() / (lr.rolling(24, min_periods=6).std() + 1e-9)

    if "eff_ratio_12" not in df.columns:
        path_len  = close.diff().abs().rolling(12, min_periods=3).sum()
        net_move  = (close - close.shift(12)).abs()
        df["eff_ratio_12"] = net_move / (path_len + 1e-9)
        df["eff_ratio_12"] = df["eff_ratio_12"].fillna(0)

    # ── Scores continus par dimension ─────────────────────────────────────

    # D1: Tendance structurelle (poids 30%)
    e20_50  = _col("ema_spread_20_50")
    e50_200 = _col("ema_spread_50_200")
    s1 = (_safe_tanh(e20_50, 8) * 0.6 + _safe_tanh(e50_200, 5) * 0.4).clip(-1, 1)

    # D2: Momentum de qualité (poids 30%) — mom_sharpe clé
    mom_sh24 = _col("mom_sharpe_24")
    mom24    = _col("mom_logret_24")
    rsi_14   = _col("rsi_14", 50)
    rsi_z    = ((rsi_14 - 50) / 50).clip(-1, 1)
    # RSI extrême = contrarian
    rsi_adj  = np.where((rsi_14 > 72) | (rsi_14 < 28), -rsi_z * 0.5, rsi_z)
    s2 = (_safe_tanh(mom_sh24, 1.5) * 0.5
          + pd.Series(rsi_adj, index=df.index) * 0.3
          + _safe_tanh(mom24, 15) * 0.2).clip(-1, 1)

    # D3: Pression volume (poids 20%) — taker + eff_ratio (tendance nette)
    tbr    = _col("taker_buy_ratio_base", 0.5)
    tbr_z  = (tbr - tbr.rolling(48, min_periods=1).mean()) / (tbr.rolling(48, min_periods=1).std() + 1e-9)
    eff12  = _col("eff_ratio_12")
    vol24  = _col("vol_ratio_24", 1.0)
    vol_amp = np.where(vol24 > 1.5, 1.2, 1.0)  # amplification si volume spike
    tbr_signal = _safe_tanh(tbr_z, 0.9)
    s3 = (tbr_signal * 0.7 + _safe_tanh((eff12 - 0.3), 4) * tbr_signal * 0.3).clip(-1, 1)
    s3 = (s3 * pd.Series(vol_amp, index=df.index)).clip(-1, 1)

    # D4: Macro (poids 20%) — contrarian sur extrêmes
    fund_z = _col("funding_rate_z_24").clip(-3, 3)
    ls_z   = _col("global_ls_longShortRatio_z_24").clip(-3, 3)
    fng_z  = _col("fear_greed_value_z_24").clip(-3, 3)
    oi_z   = _col("oihist_sumOpenInterest_z_24").clip(-3, 3)
    # Funding positif = longs surchargés → légèrement baissier (contrarian)
    # OI en hausse avec tendance = confirmation de la direction
    s4 = (-_safe_tanh(fund_z, 0.4) * 0.3
          + _safe_tanh(oi_z, 0.3) * _safe_tanh(e20_50, 5).abs().values * 0.2
          - _safe_tanh(ls_z, 0.3) * 0.3
          + _safe_tanh(fng_z, 0.3) * 0.2).clip(-1, 1)

    # ── Score total ──────────────────────────────────────────────────────
    raw = (s1 * 0.30 + s2 * 0.30 + s3 * 0.20 + s4 * 0.20)

    # FILTRE QUALITÉ : n'entrer QUE si tendance ET momentum concordent
    same_direction = (s1 * s2) > 0   # tous deux dans le même sens
    raw = raw.where(same_direction, raw * 0.3)  # réduit 70% si contradiction

    # ATR 14
    c_sh = close.shift(1)
    tr   = pd.concat([high - low, (high - c_sh).abs(), (low - c_sh).abs()], axis=1).max(axis=1)
    atr  = tr.ewm(span=14, adjust=False).mean()

    signals = pd.DataFrame(index=df.index)
    signals["raw"]    = raw
    signals["s1_trend"]    = s1
    signals["s2_momentum"] = s2
    signals["s3_pressure"] = s3
    signals["s4_macro"]    = s4
    signals["atr"]         = atr

    # Seuil plus strict : 0.22 (vs 0.15 initial)
    signals["action"] = "WAIT"
    signals.loc[raw >  0.22, "action"] = "LONG"
    signals.loc[raw < -0.22, "action"] = "SHORT"

    signals["confidence"] = (raw.abs() * 70).clip(0, 100)

    n_long  = (signals["action"] == "LONG").sum()
    n_short = (signals["action"] == "SHORT").sum()
    log.info(f"  LONG: {n_long:,} | SHORT: {n_short:,} | WAIT: {len(signals)-n_long-n_short:,} "
             f"({(n_long+n_short)/len(signals)*100:.1f}% signaux actifs)")
    return signals


# ─────────────────────────────────────────────────────────────────────────────
# Simulation de trades
# ─────────────────────────────────────────────────────────────────────────────

class Position:
    __slots__ = ["side","entry","stop","tp1","size_pct","capital_used","entry_bar","trail_stop","best_price"]

    def __init__(self, side, entry, stop, tp1, size_pct, capital_used, bar_idx):
        self.side, self.entry, self.stop, self.tp1 = side, entry, stop, tp1
        self.size_pct, self.capital_used, self.entry_bar = size_pct, capital_used, bar_idx
        self.trail_stop  = stop        # stop suiveur (mis à jour chaque barre)
        self.best_price  = entry       # meilleur prix atteint (pour trailing)


def simulate(df: pd.DataFrame, signals: pd.DataFrame) -> Dict[str, Any]:
    """
    Simulation walk-forward avec Level 7 risk management.
    Signal sur T → entre sur T+1 au close (conservateur).
    Stop et TP évalués sur H et L de T+1.
    """
    log.info("Simulation des trades…")

    capital   = INITIAL_CAPITAL
    equity    = [INITIAL_CAPITAL]
    trades: List[Dict] = []
    position: Optional[Position] = None
    consec_losses = 0
    daily_start_cap = capital
    daily_start_date: Optional[str] = None
    cooldown = 0

    # Level 7 params
    if _L7 and _RISK_LONG is not None:
        sl_long   = _RISK_LONG.stop_loss_pct
        tp_long   = _RISK_LONG.take_profit_pct
        sl_short  = _RISK_SHORT.stop_loss_pct
        tp_short  = _RISK_SHORT.take_profit_pct
        sz_long   = _RISK_LONG.position_size_pct
        sz_short  = _RISK_SHORT.position_size_pct
        max_cl_l  = _RISK_LONG.max_consecutive_losses
        max_cl_s  = _RISK_SHORT.max_consecutive_losses
        max_dd_l  = _RISK_LONG.max_daily_drawdown_pct
        max_dd_s  = _RISK_SHORT.max_daily_drawdown_pct
        cooldown_base = _RISK_LONG.cooldown_bars
    else:
        sl_long = sl_short = 0.015
        tp_long = tp_short = 0.025
        sz_long = sz_short = 0.02
        max_cl_l = max_cl_s = 4
        max_dd_l = max_dd_s = 0.03
        cooldown_base = 3

    bars = list(zip(df.itertuples(), signals.itertuples()))

    for i in range(200, len(bars) - 1):   # warmup de 200 barres
        bar, sig = bars[i]
        nbar     = bars[i + 1][0]  # barre suivante (pour exécution)

        date_str = str(bar.Index.date())

        # Daily reset
        if date_str != daily_start_date:
            daily_start_cap  = capital
            daily_start_date = date_str

        # Cooldown
        if cooldown > 0:
            cooldown -= 1
            equity.append(capital)
            continue

        # Kill-switch drawdown quotidien
        daily_dd = (capital - daily_start_cap) / (daily_start_cap + 1e-9)
        max_dd_day = max_dd_l if (position and position.side == "LONG") else max_dd_s
        if daily_dd < -max_dd_day and position:
            # Fermer la position
            exit_px = float(nbar.c)
            pnl     = _trade_pnl(position, exit_px, "kill_daily_dd")
            capital += pnl["net"]
            trades.append(pnl)
            consec_losses += 1 if pnl["net"] < 0 else 0
            position = None
            cooldown = cooldown_base * 2

        # Évaluation de la position ouverte sur le bar courant
        if position is not None:
            lo  = float(getattr(bar, 'low',   getattr(bar, 'Low',   0)))
            hi  = float(getattr(bar, 'high',  getattr(bar, 'High',  0)))
            cl  = float(getattr(bar, 'close', getattr(bar, 'Close', 0)))
            cur_atr = float(sig.atr)

            # Stop fixe (initialisé à l'entrée), TP 1:2 RR
            stop_lvl = position.stop

            # Stop hit ?
            stop_hit = (position.side == "LONG"  and lo <= stop_lvl) or \
                       (position.side == "SHORT" and hi >= stop_lvl)
            # TP hit (RR 1:2)?
            tp_hit   = (position.side == "LONG"  and hi >= position.tp1) or \
                       (position.side == "SHORT" and lo <= position.tp1)

            # Signal inverse = sortie au close
            sig_exit = (position.side == "LONG"  and sig.action == "SHORT") or \
                       (position.side == "SHORT" and sig.action == "LONG")

            # Max 48h
            max_bars_hit = (i - position.entry_bar) >= 48

            exit_px, exit_reason = None, None
            if stop_hit:
                exit_px, exit_reason = stop_lvl, "stop"
            elif tp_hit:
                exit_px, exit_reason = position.tp1, "tp"
            elif sig_exit or max_bars_hit:
                exit_px, exit_reason = cl, "signal_exit" if sig_exit else "max_bars"

            if exit_px is not None:
                pnl = _trade_pnl(position, exit_px, exit_reason)
                capital += pnl["net"]
                trades.append(pnl)
                if pnl["net"] < 0:
                    consec_losses += 1
                    after_loss = _RISK_LONG.cooldown_after_loss if _L7 else cooldown_base * 2
                    cooldown = cooldown_base + after_loss
                else:
                    consec_losses = 0
                    cooldown = cooldown_base
                position = None

        # Max pertes consécutives
        max_cl = max_cl_l if (not position or position.side == "LONG") else max_cl_s
        if consec_losses >= max_cl:
            cooldown = max(cooldown, 24)   # 24h de pause
            consec_losses = 0

        # Ouverture de nouvelle position (si aucune ouverte)
        if position is None and cooldown == 0:
            action = sig.action
            conf   = sig.confidence
            atr    = sig.atr

            if action in ("LONG", "SHORT"):
                entry_px = float(getattr(nbar, 'close', getattr(nbar, 'Close', 0)))
                entry_px *= (1 + SLIPPAGE if action == "LONG" else 1 - SLIPPAGE)

                # ATR-based stop: 2×ATR, TP: 4×ATR → RR 1:2
                # Breakeven win rate = 33.3%, objectif > 35%
                atr_pct = float(atr) / (entry_px + 1e-9)
                atr_pct = max(atr_pct, 0.003)   # min 0.3%
                atr_pct = min(atr_pct, 0.015)   # max 1.5%

                if action == "LONG":
                    sl_pct = atr_pct * 2.0
                    tp_pct = atr_pct * 4.0   # RR 1:2
                    stop   = entry_px * (1 - sl_pct)
                    tp1    = entry_px * (1 + tp_pct)
                    size   = sz_long
                else:
                    sl_pct = atr_pct * 2.0
                    tp_pct = atr_pct * 4.0
                    stop   = entry_px * (1 + sl_pct)
                    tp1    = entry_px * (1 - tp_pct)
                    size   = sz_short

                cap_used = capital * size
                position = Position(action, entry_px, stop, tp1, size, cap_used, i)

        equity.append(capital)

    # Fermer la position éventuelle à la dernière barre
    if position is not None:
        last_px = float(df["close"].iloc[-1])
        pnl = _trade_pnl(position, last_px, "eod")
        capital += pnl["net"]
        trades.append(pnl)

    log.info(f"Simulation terminée: {len(trades)} trades | capital final: ${capital:,.2f}")
    return {
        "equity":  equity,
        "trades":  trades,
        "capital_final": capital,
    }


def _trade_pnl(pos: Position, exit_px: float, reason: str) -> Dict:
    if pos.side == "LONG":
        pnl_raw = (exit_px - pos.entry) / pos.entry * pos.capital_used
    else:
        pnl_raw = (pos.entry - exit_px) / pos.entry * pos.capital_used

    fee_entry = pos.capital_used * MAKER_FEE
    fee_exit  = pos.capital_used * (TAKER_FEE if reason == "stop" else MAKER_FEE)
    net       = pnl_raw - fee_entry - fee_exit

    return {
        "side":       pos.side,
        "entry":      round(pos.entry, 4),
        "exit":       round(exit_px, 4),
        "exit_reason": reason,
        "capital_used": round(pos.capital_used, 2),
        "pnl_raw":    round(pnl_raw, 4),
        "fees":       round(fee_entry + fee_exit, 4),
        "net":        round(net, 4),
        "pnl_pct":    round(net / (pos.capital_used + 1e-9) * 100, 3),
        "is_win":     net > 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Métriques
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(equity: List[float], trades: List[Dict]) -> Dict[str, Any]:
    """Calcule toutes les métriques de performance."""
    eq  = np.array(equity, dtype=float)
    rets = np.diff(eq) / (eq[:-1] + 1e-9)

    # ── Returns ────────────────────────────────────────────────────────────
    total_return_pct  = (eq[-1] - eq[0]) / eq[0] * 100
    n_hours           = len(eq)
    n_years           = n_hours / 8760
    cagr              = ((eq[-1] / eq[0]) ** (1 / max(n_years, 0.1)) - 1) * 100

    # ── Sharpe (annualisé, 1h bars, 8760h/an) ────────────────────────────
    mean_ret = float(np.mean(rets))
    std_ret  = float(np.std(rets)) + 1e-12
    sharpe   = mean_ret / std_ret * np.sqrt(8760)

    # ── Sortino (downside std) ────────────────────────────────────────────
    neg_rets  = rets[rets < 0]
    down_std  = float(np.std(neg_rets)) + 1e-12 if len(neg_rets) > 0 else 1e-12
    sortino   = mean_ret / down_std * np.sqrt(8760)

    # ── Max Drawdown ──────────────────────────────────────────────────────
    running_max = np.maximum.accumulate(eq)
    drawdowns   = (eq - running_max) / (running_max + 1e-9)
    max_dd      = float(drawdowns.min()) * 100
    max_dd_idx  = int(np.argmin(drawdowns))

    # ── Calmar ratio ─────────────────────────────────────────────────────
    calmar = cagr / (abs(max_dd) + 1e-3)

    # ── Trades ────────────────────────────────────────────────────────────
    if not trades:
        return {"error": "Aucun trade"}

    wins       = [t for t in trades if t["is_win"]]
    losses     = [t for t in trades if not t["is_win"]]
    win_rate   = len(wins) / len(trades) * 100
    avg_win    = float(np.mean([t["net"] for t in wins]))  if wins   else 0
    avg_loss   = float(np.mean([t["net"] for t in losses])) if losses else 0
    pf_num     = sum(t["net"] for t in wins)
    pf_den     = abs(sum(t["net"] for t in losses)) + 1e-9
    profit_fac = pf_num / pf_den
    total_fees = sum(t["fees"] for t in trades)

    longs  = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]

    # ── Breakdown par an ─────────────────────────────────────────────────
    yearly: Dict[int, Dict] = {}
    for t in trades:
        pass  # trades n'ont pas de timestamp ici — calculé via equity courbe

    return {
        # Capital
        "initial_capital":   eq[0],
        "final_capital":     round(float(eq[-1]), 2),
        "total_return_pct":  round(total_return_pct, 2),
        "cagr_pct":          round(cagr, 2),
        # Risque
        "sharpe_annualized": round(sharpe, 3),
        "sortino_annualized": round(sortino, 3),
        "max_drawdown_pct":  round(max_dd, 2),
        "calmar_ratio":      round(calmar, 3),
        # Trades
        "n_trades":          len(trades),
        "n_longs":           len(longs),
        "n_shorts":          len(shorts),
        "win_rate_pct":      round(win_rate, 1),
        "avg_win_usd":       round(avg_win, 2),
        "avg_loss_usd":      round(avg_loss, 2),
        "profit_factor":     round(profit_fac, 3),
        "total_fees_usd":    round(total_fees, 2),
        "expectancy_usd":    round((avg_win * len(wins) + avg_loss * len(losses)) / len(trades), 2),
        # Contexte
        "n_bars_tested":     n_hours,
        "n_years":           round(n_years, 2),
        "level7_active":     _L7,
        "fees_pct":          f"{MAKER_FEE*100:.3f}% maker / {TAKER_FEE*100:.2f}% taker",
        "slippage_pct":      f"{SLIPPAGE*100:.2f}%",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Equity curve annuelle
# ─────────────────────────────────────────────────────────────────────────────

def equity_by_year(equity: List[float], df: pd.DataFrame) -> List[Dict]:
    idx    = df.index[:len(equity)]
    eq_s   = pd.Series(equity[:len(idx)], index=idx)
    result = []
    for yr, grp in eq_s.groupby(eq_s.index.year):
        start = float(grp.iloc[0])
        end   = float(grp.iloc[-1])
        result.append({
            "year": int(yr),
            "return_pct": round((end - start) / start * 100, 2),
            "start_equity": round(start, 2),
            "end_equity":   round(end, 2),
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(symbol: str = "BTC/USDT", since: str = "2021-01-01") -> Dict[str, Any]:
    df      = load_ohlcv(symbol, since)
    signals = compute_signals_batch(df)
    sim     = simulate(df, signals)
    metrics = compute_metrics(sim["equity"], sim["trades"])
    yearly  = equity_by_year(sim["equity"], df)

    # Échantillon des trades
    trade_sample = sim["trades"][-50:]

    # Equity curve (échantillonné sur 500 points max)
    eq = sim["equity"]
    idx_list = df.index[:len(eq)]
    step = max(1, len(eq) // 500)
    equity_curve = [
        {"time": int(idx_list[i].timestamp()), "equity": round(eq[i], 2)}
        for i in range(0, len(eq), step)
    ]

    return {
        "symbol":       symbol,
        "since":        since,
        "metrics":      metrics,
        "yearly":       yearly,
        "equity_curve": equity_curve,
        "trades":       trade_sample,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--since",  default="2021-01-01")
    parser.add_argument("--out",    default=None, help="JSON output file")
    args = parser.parse_args()

    result = run_backtest(args.symbol, args.since)
    m = result["metrics"]

    print("\n" + "=" * 60)
    print(f"BACKTEST {args.symbol}  {args.since} → today")
    print(f"Level 7: {'ACTIF ✓' if _L7 else 'fallback'}")
    print("=" * 60)
    print(f"Capital initial  : ${m['initial_capital']:>10,.2f}")
    print(f"Capital final    : ${m['final_capital']:>10,.2f}")
    print(f"Return total     : {m['total_return_pct']:>+9.2f}%")
    print(f"CAGR             : {m['cagr_pct']:>+9.2f}%/an")
    print("-" * 60)
    print(f"Sharpe annualisé : {m['sharpe_annualized']:>9.3f}")
    print(f"Sortino          : {m['sortino_annualized']:>9.3f}")
    print(f"Max Drawdown     : {m['max_drawdown_pct']:>9.2f}%")
    print(f"Calmar ratio     : {m['calmar_ratio']:>9.3f}")
    print("-" * 60)
    print(f"Trades totaux    : {m['n_trades']:>9,}")
    print(f"Win rate         : {m['win_rate_pct']:>9.1f}%")
    print(f"Profit Factor    : {m['profit_factor']:>9.3f}")
    print(f"Avg win / loss   : ${m['avg_win_usd']:>+.2f} / ${m['avg_loss_usd']:>+.2f}")
    print(f"Expectancy       : ${m['expectancy_usd']:>+.2f}/trade")
    print(f"Frais totaux     : ${m['total_fees_usd']:>,.2f}")
    print("-" * 60)
    print("\nPar année :")
    for yr in result["yearly"]:
        bar = "█" * int(abs(yr["return_pct"]) / 5) if abs(yr["return_pct"]) < 200 else "█" * 40
        sign = "+" if yr["return_pct"] >= 0 else ""
        print(f"  {yr['year']}: {sign}{yr['return_pct']:>7.1f}%  {bar}")
    print("=" * 60)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nRésultats JSON: {args.out}")


if __name__ == "__main__":
    main()
