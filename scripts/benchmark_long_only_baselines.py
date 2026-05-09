#!/usr/bin/env python3
"""
scripts/benchmark_long_only_baselines.py — COMPARAISON BASELINES LONG-ONLY
==========================================================================

Compare la stratégie LONG-only contre des baselines simples.

Baselines :
  1. Buy & Hold BTC
  2. EMA 20/50 crossover long-only
  3. RSI oversold recovery (RSI < 35 → enter, RSI > 60 → exit)
  4. Always cash (return = 0)
  5. Random entries à même fréquence que le modèle

Le modèle LONG doit au minimum battre :
  - random same-frequency (sinon l'edge est du hasard)
  - always cash (sinon il perd de l'argent net)

Usage :
  python scripts/benchmark_long_only_baselines.py
  python scripts/benchmark_long_only_baselines.py --since 2024-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
import random as _random

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.validation_engine import (
    load_alpha_data, load_models, generate_signals,
    run_backtest_core, BacktestParams, MAKER_FEE, TAKER_FEE, SLIPPAGE, RISK_FREE_ANNUAL,
)

REPORT_DIR = ROOT / "reports" / "long_only_validation"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ── Métriques communes ────────────────────────────────────────────────────────

def _metrics_from_equity(equity_curve: List[float], equity0: float, df: pd.DataFrame, trades: List = None) -> Dict:
    eq = np.array(equity_curve, dtype=float)
    final = float(eq[-1])
    total_ret = (final - equity0) / equity0 * 100

    daily_eq  = eq[::24] if len(eq) > 24 else eq
    daily_ret = np.diff(daily_eq) / (daily_eq[:-1] + 1e-9)
    rf_daily  = RISK_FREE_ANNUAL / 252
    excess    = daily_ret - rf_daily
    sharpe    = float((excess.mean() / (excess.std() + 1e-9)) * np.sqrt(252)) if len(excess) > 1 else 0.0
    neg_ret   = daily_ret[daily_ret < 0]
    sortino   = float((excess.mean() / (neg_ret.std() + 1e-9)) * np.sqrt(252)) if len(neg_ret) > 1 else 0.0

    run_max  = np.maximum.accumulate(eq)
    dds      = (eq - run_max) / (run_max + 1e-9)
    max_dd   = float(dds.min()) * 100

    calmar   = total_ret / (abs(max_dd) + 1e-9)

    # Exposition
    n_trades   = len(trades) if trades else 0
    bars_held  = sum(t.get("bars_held", 1) for t in trades) if trades else 0
    exp_pct    = bars_held / max(len(df), 1) * 100

    pnls    = np.array([t["net_pnl"] for t in trades]) if trades else np.array([])
    wins    = pnls[pnls > 0]
    losses  = pnls[pnls < 0]
    pf      = float(wins.sum() / abs(losses.sum())) if len(losses) > 0 and abs(losses.sum()) > 1e-9 else float("inf")
    exp_per_trade = float(pnls.mean()) if len(pnls) > 0 else 0.0
    win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0.0
    turnover = sum(t.get("fees", 0) for t in trades) / equity0 if trades else 0.0

    return_per_exposure = total_ret / max(exp_pct, 1e-9)

    return {
        "n_trades":            n_trades,
        "total_return_pct":    round(total_ret, 2),
        "max_drawdown_pct":    round(max_dd, 2),
        "sharpe":              round(sharpe, 3),
        "sortino":             round(sortino, 3),
        "calmar":              round(calmar, 3),
        "profit_factor":       round(pf, 3) if pf != float("inf") else None,
        "expectancy":          round(exp_per_trade, 4) if trades else None,
        "win_rate":            round(win_rate, 4) if trades else None,
        "exposure_pct":        round(exp_pct, 2),
        "return_per_exposure": round(return_per_exposure, 4),
        "turnover":            round(turnover, 6),
        "final_equity":        round(final, 2),
    }


# ── Baseline 1 : Buy and Hold ──────────────────────────────────────────────────

def baseline_buy_and_hold(df: pd.DataFrame, equity0: float = 10_000.0) -> Dict:
    close = df["close"].values.astype(float)
    n = len(close)
    ret = (close[-1] - close[0]) / close[0]
    eq_curve = equity0 * (close / close[0])
    m = _metrics_from_equity(eq_curve.tolist(), equity0, df)
    m["strategy"] = "buy_and_hold"
    m["n_trades"] = 1
    m["exposure_pct"] = 100.0
    return m


# ── Baseline 2 : EMA 20/50 ────────────────────────────────────────────────────

def baseline_ema_crossover(df: pd.DataFrame, equity0: float = 10_000.0) -> Dict:
    close = df["close"].values.astype(float)
    n = len(close)

    ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values
    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values

    equity = equity0
    equity_curve = [equity]
    trades = []
    in_trade = False
    entry_price = 0.0
    entry_bar = -1

    for i in range(50, n):
        if not in_trade:
            if ema20[i] > ema50[i] and ema20[i - 1] <= ema50[i - 1]:
                entry_price = close[i] * (1 + SLIPPAGE)
                in_trade = True
                entry_bar = i
        else:
            if ema20[i] < ema50[i]:
                exit_price = close[i] * (1 - SLIPPAGE)
                pnl = (exit_price - entry_price) / entry_price * equity0 * 0.02
                fee = equity0 * 0.02 * (MAKER_FEE * 2 + SLIPPAGE * 2)
                net = pnl - fee
                equity += net
                trades.append({
                    "bars_held": i - entry_bar, "net_pnl": net, "fees": fee,
                    "entry_price": entry_price, "exit_price": exit_price,
                })
                in_trade = False
        equity_curve.append(equity)

    m = _metrics_from_equity(equity_curve, equity0, df, trades)
    m["strategy"] = "ema_20_50_crossover"
    return m


# ── Baseline 3 : RSI oversold recovery ────────────────────────────────────────

def baseline_rsi_oversold(df: pd.DataFrame, equity0: float = 10_000.0, rsi_entry: float = 35.0, rsi_exit: float = 60.0) -> Dict:
    close = df["close"].values.astype(float)
    n = len(close)

    if "rsi_14" in df.columns:
        rsi = df["rsi_14"].values
    else:
        delta = np.diff(close, prepend=close[0])
        gain  = np.where(delta > 0, delta, 0)
        loss  = np.where(delta < 0, -delta, 0)
        avg_g = pd.Series(gain).ewm(com=13, adjust=False).mean().values
        avg_l = pd.Series(loss).ewm(com=13, adjust=False).mean().values
        rs    = avg_g / (avg_l + 1e-9)
        rsi   = 100 - 100 / (1 + rs)

    equity = equity0
    equity_curve = [equity]
    trades = []
    in_trade = False
    entry_price = 0.0
    entry_bar = -1

    for i in range(14, n):
        if not in_trade:
            if rsi[i] < rsi_entry:
                entry_price = close[i] * (1 + SLIPPAGE)
                in_trade = True
                entry_bar = i
        else:
            if rsi[i] > rsi_exit:
                exit_price = close[i] * (1 - SLIPPAGE)
                pnl = (exit_price - entry_price) / entry_price * equity0 * 0.02
                fee = equity0 * 0.02 * (MAKER_FEE * 2 + SLIPPAGE * 2)
                net = pnl - fee
                equity += net
                trades.append({
                    "bars_held": i - entry_bar, "net_pnl": net, "fees": fee,
                    "entry_price": entry_price, "exit_price": exit_price,
                })
                in_trade = False
        equity_curve.append(equity)

    m = _metrics_from_equity(equity_curve, equity0, df, trades)
    m["strategy"] = "rsi_oversold_recovery"
    return m


# ── Baseline 4 : Always cash ──────────────────────────────────────────────────

def baseline_always_cash(df: pd.DataFrame, equity0: float = 10_000.0) -> Dict:
    m = _metrics_from_equity([equity0] * len(df), equity0, df, [])
    m["strategy"] = "always_cash"
    m["n_trades"] = 0
    return m


# ── Baseline 5 : Random same-frequency ───────────────────────────────────────

def baseline_random_same_freq(
    df: pd.DataFrame,
    model_trade_freq: float,
    equity0: float = 10_000.0,
    n_simulations: int = 50,
    seed: int = 42,
) -> Dict:
    """
    Entrée aléatoire avec la même fréquence de trades que le modèle.
    Moyenne sur n_simulations pour stabiliser.
    """
    _random.seed(seed)
    np.random.seed(seed)

    close = df["close"].values.astype(float)
    n = len(close)
    stop_pct = 0.015
    tp_pct   = 0.025

    all_metrics = []
    for sim in range(n_simulations):
        equity = equity0
        equity_curve = [equity]
        trades = []
        in_trade = False
        entry_price = 0.0
        entry_bar = -1
        stop_price = 0.0
        tp_price = 0.0

        for i in range(1, n):
            high = float(df["high"].iloc[i]) if "high" in df.columns else close[i]
            low  = float(df["low"].iloc[i])  if "low"  in df.columns else close[i]

            if in_trade:
                if low <= stop_price:
                    exit_price = stop_price; exit_reason = "stop"
                elif high >= tp_price:
                    exit_price = tp_price;   exit_reason = "tp"
                else:
                    equity_curve.append(equity); continue

                bars_held = i - entry_bar
                notional  = equity * 0.02
                pnl_raw   = (exit_price - entry_price) / entry_price * notional
                fee       = notional * (MAKER_FEE + TAKER_FEE + 2 * SLIPPAGE)
                net       = pnl_raw - fee
                equity   += net
                trades.append({"bars_held": bars_held, "net_pnl": net, "fees": fee,
                                "entry_price": entry_price, "exit_price": exit_price})
                equity_curve.append(equity)
                in_trade = False
            else:
                if _random.random() < model_trade_freq:
                    entry_price = close[i] * (1 + SLIPPAGE)
                    stop_price  = entry_price * (1 - stop_pct)
                    tp_price    = entry_price * (1 + tp_pct)
                    entry_bar   = i
                    in_trade    = True
                equity_curve.append(equity)

        all_metrics.append(_metrics_from_equity(equity_curve, equity0, df, trades))

    # Moyenne des simulations
    keys = [k for k in all_metrics[0] if isinstance(all_metrics[0][k], (int, float))]
    avg = {}
    for k in keys:
        vals = [m[k] for m in all_metrics if m[k] is not None]
        avg[k] = round(float(np.mean(vals)), 4) if vals else None

    avg["strategy"] = "random_same_frequency"
    avg["n_simulations"] = n_simulations
    avg["model_trade_freq_pct"] = round(model_trade_freq * 100, 2)
    return avg


# ── Comparaison ───────────────────────────────────────────────────────────────

def run_comparison(
    df: pd.DataFrame,
    models,
    filter_threshold: float = 0.51,
    edge_threshold: float = 0.58,
    equity0: float = 10_000.0,
) -> Dict:
    params = BacktestParams(equity0=equity0)
    df_sig = generate_signals(df, models, filter_threshold=filter_threshold, edge_threshold=edge_threshold)
    model_result = run_backtest_core(df_sig, params)

    n_model = model_result.get("n_trades", 0)
    trade_freq = n_model / max(len(df), 1)

    close_col = "Close" if "Close" in df.columns else "close"
    df["close"] = df[close_col]
    if "High" in df.columns: df["high"] = df["High"]
    if "Low"  in df.columns: df["low"]  = df["Low"]

    baselines = {
        "model_long_only":      {**{k: model_result.get(k) for k in [
            "n_trades", "total_return_pct", "max_drawdown_pct", "sharpe", "sortino",
            "calmar", "profit_factor", "expectancy", "win_rate", "exposure_pct",
            "turnover", "deployable", "status", "reason",
        ]}, "strategy": "model_long_only"},
        "buy_and_hold":         baseline_buy_and_hold(df, equity0),
        "ema_20_50":            baseline_ema_crossover(df, equity0),
        "rsi_oversold":         baseline_rsi_oversold(df, equity0),
        "always_cash":          baseline_always_cash(df, equity0),
        "random_same_frequency": baseline_random_same_freq(df, trade_freq, equity0),
    }

    for strat, m in baselines.items():
        m["return_per_exposure"] = round(
            m.get("total_return_pct", 0) / max(m.get("exposure_pct", 1e-9), 1e-9), 4
        )

    return baselines


def print_comparison(baselines: Dict, model_key: str = "model_long_only") -> None:
    sep = "─" * 80
    print(f"\n{sep}")
    print("COMPARAISON BASELINES LONG-ONLY")
    print(sep)

    headers = ["Stratégie", "Trades", "Retour%", "MaxDD%", "Sharpe", "Sortino", "Ret/Exp"]
    print(f"{'Stratégie':<28} {'Trades':>7} {'Ret%':>7} {'DD%':>7} {'Sharpe':>8} {'Sortino':>8} {'Ret/Exp':>8}")
    print("─" * 80)

    model_ret = baselines[model_key].get("total_return_pct", 0)
    model_dd  = baselines[model_key].get("max_drawdown_pct", 0)

    for strat, m in baselines.items():
        ret = m.get("total_return_pct", 0) or 0
        dd  = m.get("max_drawdown_pct", 0) or 0
        sh  = m.get("sharpe", 0) or 0
        so  = m.get("sortino", 0) or 0
        rpe = m.get("return_per_exposure", 0) or 0
        nt  = m.get("n_trades", 0) or 0
        mark = " ←" if strat == model_key else ""
        print(
            f"{strat:<28} {int(nt):>7} {ret:>+7.2f} {dd:>7.2f} {sh:>8.3f} {so:>8.3f} {rpe:>8.4f}{mark}"
        )

    print(sep)

    model = baselines[model_key]
    random_bsl = baselines.get("random_same_frequency", {})
    cash_bsl   = baselines.get("always_cash", {})

    beats_random = (model.get("total_return_pct", 0) or 0) > (random_bsl.get("total_return_pct", 0) or 0)
    beats_cash   = (model.get("total_return_pct", 0) or 0) > 0
    better_dd    = abs(model.get("max_drawdown_pct", 0) or 0) < abs(baselines.get("buy_and_hold", {}).get("max_drawdown_pct", 100) or 100)

    print(f"\n✓ Bat random same-freq : {'OUI' if beats_random else 'NON'}")
    print(f"✓ Bat always cash      : {'OUI' if beats_cash   else 'NON'}")
    print(f"✓ Meilleur DD que B&H  : {'OUI' if better_dd    else 'NON'}")

    if not beats_random:
        print("\n⚠  ATTENTION : le modèle ne bat pas le random. Aucun edge démontré.")
    elif beats_random and beats_cash:
        print("\n✓ Edge minimal démontré vs random et cash.")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="Comparaison baselines LONG-only")
    parser.add_argument("--since", default=None,   help="Date début ISO")
    parser.add_argument("--ft",    default=0.51,   type=float, help="Filter threshold")
    parser.add_argument("--dt",    default=0.58,   type=float, help="Direction threshold")
    args = parser.parse_args()

    print("Chargement données et modèles…")
    df     = load_alpha_data(since=args.since)
    models = load_models()

    print(f"Comparaison baselines (ft={args.ft}, dt={args.dt})…")
    baselines = run_comparison(df, models, args.ft, args.dt)
    print_comparison(baselines)

    (REPORT_DIR / "baseline_comparison.json").write_text(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in baselines.items()}, indent=2)
    )
    df_bsl = pd.DataFrame([
        {k: v for k, v in m.items() if not isinstance(v, (list, dict))}
        for m in baselines.values()
    ])
    df_bsl.to_csv(REPORT_DIR / "baseline_comparison.csv", index=False)

    print(f"\nRésultats sauvegardés dans {REPORT_DIR}/")


if __name__ == "__main__":
    main()
