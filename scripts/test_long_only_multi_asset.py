#!/usr/bin/env python3
"""
scripts/test_long_only_multi_asset.py — TEST MULTI-ACTIF LONG-ONLY
===================================================================

Teste le pipeline LONG-only (modèles BTC) sur BTC, ETH, SOL.

Note : les modèles sont entraînés sur BTC. Appliquer à ETH/SOL
est un test de généralisation, pas un déploiement immédiat.

Interprétation :
  - BTC seul viable : edge spécifique BTC, prudence max
  - BTC+ETH cohérents : edge potentiellement généralisable
  - BTC+ETH+SOL cohérents : edge crypto général
  - ETH/SOL échouent : overfitting BTC ou caractéristiques spécifiques

Usage :
  python scripts/test_long_only_multi_asset.py
  python scripts/test_long_only_multi_asset.py --since 2024-01-01
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.validation_engine import (
    load_alpha_data, load_models, generate_signals,
    run_backtest_core, BacktestParams,
)

REPORT_DIR = ROOT / "reports" / "long_only_validation"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = ROOT / "data" / "ohlcv_1m"


def _resample_to_1h(parquet_files: List[Path]) -> Optional[pd.DataFrame]:
    """Agrège les fichiers 1m Parquet en 1h OHLCV."""
    dfs = []
    for f in sorted(parquet_files):
        try:
            dfs.append(pd.read_parquet(f))
        except Exception as e:
            print(f"  Impossible de lire {f.name}: {e}")

    if not dfs:
        return None

    df = pd.concat(dfs, ignore_index=True)

    # Normalise timestamp
    for col in ["open_time", "timestamp", "time", "datetime"]:
        if col in df.columns:
            df["datetime"] = pd.to_datetime(df[col], utc=True)
            break

    if "datetime" not in df.columns:
        return None

    df = df.sort_values("datetime").set_index("datetime")

    # Normalise colonnes OHLCV
    col_map = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
    for std, variants in [
        ("open",   ["open", "Open"]),
        ("high",   ["high", "High"]),
        ("low",    ["low",  "Low"]),
        ("close",  ["close", "Close"]),
        ("volume", ["volume", "Volume", "quote_volume", "Quote_Volume"]),
    ]:
        for v in variants:
            if v in df.columns:
                df[std] = pd.to_numeric(df[v], errors="coerce")
                break

    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = np.nan

    # Resample 1h
    df_1h = df[["open", "high", "low", "close", "volume"]].resample("1H").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["close"])

    df_1h = df_1h.reset_index()
    return df_1h


def _compute_features_minimal(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute les features minimales pour que les modèles tournent (avec 0 pour les manquantes)."""
    df = df.copy()
    close = df["close"].values.astype(float)
    high  = df["high"].values.astype(float)  if "high"   in df.columns else close
    low   = df["low"].values.astype(float)   if "low"    in df.columns else close
    vol   = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(close))

    # Returns et volatilité
    log_ret = np.log(close / (np.roll(close, 1) + 1e-9))
    log_ret[0] = 0

    def ewm_std(x, span):
        return pd.Series(x).ewm(span=span, adjust=False).std().fillna(0).values

    def ewm_mean(x, span):
        return pd.Series(x).ewm(span=span, adjust=False).mean().fillna(0).values

    df["rv_12"]  = ewm_std(log_ret, 12)
    df["rv_24"]  = ewm_std(log_ret, 24)
    df["rv_48"]  = ewm_std(log_ret, 48)
    df["rv_72"]  = ewm_std(log_ret, 72)
    df["rv_168"] = ewm_std(log_ret, 168)

    def safe_div(a, b, default=0.0):
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(np.abs(b) > 1e-9, a / b, default)
        return r

    df["rv_ratio_24_72"] = safe_div(df["rv_24"].values, df["rv_72"].values, 1.0)
    df["rv_ratio_12_48"] = safe_div(df["rv_12"].values, df["rv_48"].values, 1.0)

    # ATR
    hl = high - low
    hc = np.abs(high - np.roll(close, 1))
    lc = np.abs(low  - np.roll(close, 1))
    tr = np.maximum.reduce([hl, hc, lc])
    df["atr_14"]     = ewm_mean(tr, 14)
    df["atr_pct_14"] = safe_div(df["atr_14"].values, close, 0)

    # Bollinger
    ma20 = ewm_mean(close, 20)
    std20 = ewm_std(log_ret, 20)
    df["boll_pos_20"]  = safe_div(close - (ma20 - 2 * std20 * close),
                                   4 * std20 * close + 1e-9, 0.5)
    df["boll_width_20"] = 4 * std20

    # Intrabar
    df["close_in_bar"]     = safe_div(close - low, high - low + 1e-9, 0.5)
    df["intrabar_range_pct"] = safe_div(high - low, close + 1e-9, 0)

    # Efficiency ratio
    def eff_ratio(x, n):
        prices = pd.Series(x)
        net_move = (prices - prices.shift(n)).abs()
        path = prices.diff().abs().rolling(n).sum()
        return (net_move / (path + 1e-9)).fillna(0).values
    df["eff_ratio_12"] = eff_ratio(close, 12)
    df["eff_ratio_24"] = eff_ratio(close, 24)

    # Z-score close
    df["zscore_close_24"] = safe_div(
        close - ewm_mean(close, 24),
        ewm_std(log_ret, 24) * close + 1e-9, 0
    )

    # Momentum
    for lag in [6, 12, 24, 72]:
        df[f"mom_logret_{lag}"] = pd.Series(log_ret).rolling(lag, min_periods=1).sum().values

    # EMAs
    for span in [20, 50, 200]:
        ema = ewm_mean(close, span)
        df[f"dist_ema_{span}"] = safe_div(close - ema, ema + 1e-9, 0)
    df["ema_spread_20_50"]  = df["dist_ema_20"] - df["dist_ema_50"]
    df["ema_spread_50_200"] = df["dist_ema_50"] - df["dist_ema_200"]

    # RSI
    delta = np.diff(close, prepend=close[0])
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_g = ewm_mean(gain, 14)
    avg_l = ewm_mean(loss, 14)
    rs    = safe_div(avg_g, avg_l + 1e-9, 0)
    df["rsi_14"] = 100 - 100 / (1 + rs)

    # CCI approximation
    tp = (high + low + close) / 3
    ma_tp  = ewm_mean(tp, 20)
    md_tp  = ewm_std(tp - ma_tp, 20)
    df["cci_20"] = safe_div(tp - ma_tp, 0.015 * (md_tp + 1e-9), 0)

    # Taker buy ratio (if volume available, use 50/50)
    if "taker_buy_base" in df.columns:
        df["taker_buy_ratio_base"] = safe_div(
            df["taker_buy_base"].values, vol + 1e-9, 0.5
        )
    else:
        df["taker_buy_ratio_base"] = 0.5
    df["delta_taker_pressure"] = df["taker_buy_ratio_base"] - 0.5

    # Volume ratios
    vol_ma24 = ewm_mean(vol, 24)
    df["vol_ratio_24"]   = safe_div(vol, vol_ma24 + 1e-9, 1.0)
    df["trades_ratio_24"] = 1.0  # pas disponible
    df["trade_intensity"] = df["vol_ratio_24"]
    df["vol_imbalance"]   = df["delta_taker_pressure"]

    # Time features
    if "datetime" in df.columns:
        dt = pd.to_datetime(df["datetime"], utc=True)
        df["hour_sin"] = np.sin(2 * np.pi * dt.dt.hour / 24)
        df["hour_cos"] = np.cos(2 * np.pi * dt.dt.hour / 24)
        df["dow_sin"]  = np.sin(2 * np.pi * dt.dt.dayofweek / 7)
        df["dow_cos"]  = np.cos(2 * np.pi * dt.dt.dayofweek / 7)
    else:
        df["hour_sin"] = df["hour_cos"] = df["dow_sin"] = df["dow_cos"] = 0.0

    # Dist local low/high
    for w in [24, 168]:
        roll_low  = pd.Series(low).rolling(w, min_periods=1).min().values
        roll_high = pd.Series(high).rolling(w, min_periods=1).max().values
        df[f"dist_from_local_low_{w}"]  = safe_div(close - roll_low,  roll_low  + 1e-9, 0)
        df[f"dist_from_local_high_{w}"] = safe_div(close - roll_high, roll_high + 1e-9, 0)

    # Autres features avancées (remplies à 0 si absentes)
    for col in ["breakout_strength_24", "trend_persistence_12", "ret_pos_autocorr_12",
                "upside_vol_ratio_24", "momentum_accel_6", "boll_expansion_6",
                "taker_buy_cumul_12", "buy_vol_ratio_6", "liq_short_spike_12",
                "liq_imbalance", "funding_rate_z_24", "oihist_sumOpenInterest_z_24",
                "fear_greed_value_z_24", "taker_ls_imbalance", "oi_x_fng"]:
        if col not in df.columns:
            df[col] = 0.0

    # Close normalisée
    df["Close"] = close
    df["High"]  = high
    df["Low"]   = low

    return df


def load_asset_data(symbol: str, since: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Charge les données 1h pour un actif depuis les parquets 1m."""
    files = sorted(DATA_DIR.glob(f"{symbol}*.parquet"))
    if not files:
        print(f"  Aucun fichier pour {symbol} dans {DATA_DIR}")
        return None

    df = _resample_to_1h(files)
    if df is None:
        return None

    df = _compute_features_minimal(df)

    if since:
        df = df[df["datetime"] >= pd.Timestamp(since, tz="UTC")]

    return df.reset_index(drop=True)


def run_multi_asset(
    models,
    assets: List[str] = None,
    since: Optional[str] = None,
    filter_threshold: float = 0.51,
    edge_threshold:   float = 0.58,
) -> Dict:
    if assets is None:
        assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    params = BacktestParams()
    results = {}

    for symbol in assets:
        print(f"\n  {symbol}:")

        if symbol in ["BTCUSDT", "BTC/USDT", "BTCUSD"]:
            df = load_alpha_data(since=since)
            if "Close" in df.columns and "close" not in df.columns:
                df["close"] = df["Close"]
            if "High" in df.columns and "high" not in df.columns:
                df["high"] = df["High"]
            if "Low" in df.columns and "low" not in df.columns:
                df["low"] = df["Low"]
        else:
            df = load_asset_data(symbol, since=since)
            if df is None:
                print(f"    Données indisponibles → skip")
                results[symbol] = {"status": "no_data", "deployable": False}
                continue

        print(f"    {len(df):,} barres | {df['datetime'].min().date()} → {df['datetime'].max().date()}")

        df_sig = generate_signals(df, models, filter_threshold=filter_threshold, edge_threshold=edge_threshold)
        m = run_backtest_core(df_sig, params)

        # Breakdown annuel
        yearly_pf = m.get("yearly_profit_factor", {})
        n = m.get("n_trades", 0)
        pf = m.get("profit_factor", 0)

        flag = "✓" if m.get("deployable") else ("⚠" if n >= 10 else "✗")
        print(f"    {flag} n={n}  PF={pf:.3f}  E={m.get('expectancy', 0):+.4f}  DD={m.get('max_drawdown_pct', 0):.1f}%")
        for y, ypf in sorted(yearly_pf.items()):
            yf = "✓" if ypf >= 1.0 else "✗"
            print(f"       {y}: PF={ypf:.3f} {yf}")

        results[symbol] = {k: v for k, v in m.items() if k != "trades"}
        results[symbol]["symbol"] = symbol

    return results


def print_multi_asset_summary(results: Dict) -> None:
    sep = "─" * 70
    print(f"\n{sep}")
    print("MULTI-ACTIF LONG-ONLY — RÉSUMÉ")
    print(sep)
    print(f"{'Actif':<12} {'Trades':>7} {'PF':>6} {'E/trade':>8} {'DD%':>7} {'Deploy':>8}")
    print("─" * 70)
    for symbol, m in results.items():
        if m.get("status") == "no_data":
            print(f"{symbol:<12} {'N/A':>7}")
            continue
        dep = "✓ OUI" if m.get("deployable") else "✗ NON"
        print(
            f"{symbol:<12} {m.get('n_trades', 0):>7} {m.get('profit_factor', 0):>6.3f}"
            f" {m.get('expectancy', 0):>+8.4f} {m.get('max_drawdown_pct', 0):>7.2f} {dep:>8}"
        )
    print(sep)

    # Interprétation
    valid  = [s for s, m in results.items() if m.get("deployable")]
    partial = [s for s, m in results.items() if m.get("n_trades", 0) >= 10 and not m.get("deployable")]
    failed = [s for s, m in results.items() if m.get("n_trades", 0) < 10]

    if len(valid) >= 2:
        print("✓ Edge potentiellement généralisable (≥ 2 actifs déployables)")
    elif len(valid) == 1:
        print(f"⚠ Edge spécifique à {valid[0]} uniquement — généralisation incertaine")
    else:
        print("✗ Aucun actif ne remplit les critères de déploiement")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test multi-actif LONG-only")
    parser.add_argument("--since", default=None, help="Date début ISO")
    parser.add_argument("--ft",    default=0.51, type=float)
    parser.add_argument("--dt",    default=0.58, type=float)
    args = parser.parse_args()

    print("Chargement des modèles…")
    models = load_models()

    results = run_multi_asset(models, since=args.since,
                              filter_threshold=args.ft, edge_threshold=args.dt)
    print_multi_asset_summary(results)

    (REPORT_DIR / "multi_asset_results.json").write_text(json.dumps(results, indent=2))
    rows = [{k: v for k, v in m.items() if not isinstance(v, (dict, list))}
            for m in results.values()]
    pd.DataFrame(rows).to_csv(REPORT_DIR / "multi_asset_results.csv", index=False)

    print(f"\nRésultats sauvegardés dans {REPORT_DIR}/")


if __name__ == "__main__":
    main()
