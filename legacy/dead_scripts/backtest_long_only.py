#!/usr/bin/env python3
"""
scripts/backtest_long_only.py — BACKTEST LONG-ONLY RÉALISTE
============================================================

Source de vérité pour la stratégie LONG.

Inclut :
  - Frais maker/taker (Binance Futures)
  - Slippage + spread cost
  - Market impact simple (participation rate)
  - Funding cost hourly
  - Max Drawdown, Sharpe, Sortino, Calmar
  - Profit Factor, expectancy/trade, win rate
  - Breakdown par année
  - Benchmark buy-and-hold BTC
  - Gate de validation : déployable si ≥ 50 trades et PF ≥ 1.20

Usage :
  python scripts/backtest_long_only.py
  python scripts/backtest_long_only.py --since 2022-01-01 --out result.json
  python scripts/backtest_long_only.py --run-dir runs/pipeline/20260419-130433
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo import MongoClient, ASCENDING

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.strategy_flags import (
    MIN_LONG_TRADES_FOR_DEPLOY,
    MIN_PROFIT_FACTOR,
    MIN_EXPECTANCY,
    MAX_DRAWDOWN_PCT,
    MIN_YEARLY_PROFIT_FACTOR,
    LONG_ONLY_ENABLED,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backtest_long")

# ── Frais et coûts ─────────────────────────────────────────────────────────────

MAKER_FEE         = 0.0005   # 0.05% — limit orders
TAKER_FEE         = 0.0010   # 0.10% — market orders / stops
SLIPPAGE          = 0.0002   # 0.02% — demi-spread conservateur
FUNDING_RATE_8H   = 0.0001   # 0.01% / 8h (taux moyen haussier)
RISK_FREE_ANNUAL  = 0.05     # 5% taux sans risque (benchmark)

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


# ── Market impact ──────────────────────────────────────────────────────────────

def market_impact_bps(
    order_notional: float,
    hourly_volume_notional: float,
    volatility_bps: float,
    k: float = 0.15,
) -> float:
    """Impact marché simple (square-root model)."""
    if hourly_volume_notional <= 0:
        return 0.0
    participation = order_notional / hourly_volume_notional
    return k * (participation ** 0.5) * volatility_bps


# ── Chargement données ─────────────────────────────────────────────────────────

def load_data(run_dir: Optional[Path], since: Optional[str]) -> pd.DataFrame:
    """Charge les données OHLCV depuis MongoDB ou un run local."""
    if run_dir is not None:
        # Cherche un parquet exporté dans le run
        for pattern in ["backtest_long*.parquet", "ohlcv*.parquet", "signals*.parquet"]:
            files = list(run_dir.glob(pattern))
            if files:
                df = pd.read_parquet(files[0])
                log.info(f"Données chargées depuis {files[0].name} ({len(df):,} barres)")
                return _prepare(df)

    log.info(f"Connexion MongoDB {MONGO_URI} / {DB_NAME}")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db     = client[DB_NAME]

    query: Dict[str, Any] = {"symbol": {"$in": _symbol_variants("BTCUSDT")}}
    if since:
        query["open_time"] = {"$gte": pd.Timestamp(since, tz="UTC")}

    cursor = db[FEATURE_COLLECTION].find(query).sort("open_time", ASCENDING)
    records = list(cursor)

    if not records:
        raise RuntimeError(
            f"Aucune donnée dans {FEATURE_COLLECTION}. "
            "Lancez scripts/build_enriched_mongo_collection.py d'abord."
        )

    df = pd.DataFrame(records)
    df["ts"] = pd.to_datetime(df.get("open_time", df.get("timestamp")), utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    log.info(f"{len(df):,} barres chargées depuis MongoDB ({df['ts'].iloc[0].date()} → {df['ts'].iloc[-1].date()})")
    return _prepare(df)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).copy()
    if "ts" not in df.columns:
        ts_col = next((c for c in ["open_time", "timestamp", "time"] if c in df.columns), None)
        if ts_col:
            df["ts"] = pd.to_datetime(df[ts_col], utc=True)
    return df


# ── Chargement des signaux depuis un run ──────────────────────────────────────

def load_signals(run_dir: Path) -> Optional[pd.DataFrame]:
    """Charge les signaux pré-calculés depuis un run (trades déjà exécutés)."""
    for pattern in ["trades_long*.json", "backtest_long*/trades*.json"]:
        files = list(run_dir.glob(pattern))
        if files:
            with open(files[0]) as f:
                trades = json.load(f)
            if trades:
                return pd.DataFrame(trades)
    return None


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    signal_col: str = "long_signal",
    p_long_col: str = "p_long",
    stop_pct: float = 0.015,
    tp_pct: float = 0.025,
    equity0: float = 10_000.0,
    position_size_pct: float = 0.02,
) -> Dict[str, Any]:
    """
    Backtest LONG-only barre-à-barre.

    signal_col : colonne booléenne indiquant le signal d'entrée
    p_long_col : probabilité de hausse (utilisée pour le log)
    """
    if signal_col not in df.columns:
        raise ValueError(
            f"Colonne '{signal_col}' absente. Colonnes disponibles : {list(df.columns[:20])}"
        )

    equity   = equity0
    equity_curve: List[float] = [equity]
    trades:       List[Dict]  = []
    in_trade  = False
    entry_bar = -1
    entry_price = 0.0
    stop_price  = 0.0
    tp_price    = 0.0
    position_notional = 0.0

    for i in range(1, len(df)):
        row      = df.iloc[i]
        prev     = df.iloc[i - 1]
        price    = float(row["close"])
        high     = float(row.get("high", price))
        low      = float(row.get("low",  price))
        volume   = float(row.get("volume", 0))
        vol_notional = price * volume

        if in_trade:
            # Check stop (intrabar)
            if low <= stop_price:
                exit_price = stop_price
                exit_reason = "stop"
            # Check TP (intrabar)
            elif high >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            else:
                equity_curve.append(equity)
                continue

            # ── Calcul P&L net ────────────────────────────────────────────────
            pnl_raw   = (exit_price - entry_price) / entry_price * position_notional
            fee_entry = position_notional * (MAKER_FEE + SLIPPAGE)
            fee_exit  = position_notional * (TAKER_FEE + SLIPPAGE)

            # Funding cost : 1/3 d'une période 8h par heure (approximation)
            bars_held = i - entry_bar
            funding   = position_notional * FUNDING_RATE_8H * bars_held / 8.0

            # Market impact entrée
            vol_bps   = float(prev.get("rv_24", 0.02)) * 10_000
            mi_bps    = market_impact_bps(position_notional, vol_notional, vol_bps)
            mi_cost   = position_notional * mi_bps / 10_000

            net_pnl   = pnl_raw - fee_entry - fee_exit - funding - mi_cost
            equity   += net_pnl

            trades.append({
                "entry_bar":   entry_bar,
                "exit_bar":    i,
                "bars_held":   bars_held,
                "entry_price": round(entry_price, 2),
                "exit_price":  round(exit_price, 2),
                "exit_reason": exit_reason,
                "pnl_raw":     round(pnl_raw, 4),
                "fees":        round(fee_entry + fee_exit, 4),
                "funding":     round(funding, 4),
                "market_impact": round(mi_cost, 4),
                "net_pnl":     round(net_pnl, 4),
                "equity":      round(equity, 2),
                "year":        str(row.get("ts", pd.Timestamp.now()).year)
                               if hasattr(row.get("ts", None), "year")
                               else str(i),
            })
            equity_curve.append(equity)
            in_trade = False

        else:
            # Cherche un signal sur la barre précédente (décalage temporel correct)
            signal = bool(prev.get(signal_col, False))
            if signal:
                entry_price = price * (1 + SLIPPAGE)   # slippage d'entrée
                position_notional = equity * position_size_pct
                stop_price  = entry_price * (1 - stop_pct)
                tp_price    = entry_price * (1 + tp_pct)
                entry_bar   = i
                in_trade    = True
            equity_curve.append(equity)

    return _compute_metrics(trades, equity_curve, equity0, df)


# ── Métriques ─────────────────────────────────────────────────────────────────

def _compute_metrics(
    trades: List[Dict],
    equity_curve: List[float],
    equity0: float,
    df: pd.DataFrame,
) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "deployable": False, "reason": "no_trades", "status": "no_signal"}

    pnls     = np.array([t["net_pnl"] for t in trades])
    wins     = pnls[pnls > 0]
    losses   = pnls[pnls < 0]

    gross_profit = float(wins.sum())  if len(wins)   > 0 else 0.0
    gross_loss   = float(losses.sum()) if len(losses) > 0 else 0.0

    profit_factor = (
        gross_profit / abs(gross_loss) if abs(gross_loss) > 1e-9 else float("inf")
    )
    win_rate  = len(wins) / n
    expectancy = float(pnls.mean())
    total_fees = sum(t["fees"] + t["funding"] + t["market_impact"] for t in trades)

    # Equity curve metrics
    eq_arr = np.array(equity_curve, dtype=float)
    final_equity = float(eq_arr[-1])
    total_return = (final_equity - equity0) / equity0

    # Returns quotidiens (approximation : 1 barre = 1h)
    daily_eq = eq_arr[::24]
    daily_ret = np.diff(daily_eq) / daily_eq[:-1]

    rf_daily = RISK_FREE_ANNUAL / 252
    excess   = daily_ret - rf_daily
    sharpe   = (excess.mean() / (excess.std() + 1e-9)) * np.sqrt(252) if len(excess) > 1 else 0.0

    neg_ret  = daily_ret[daily_ret < 0]
    sortino  = (excess.mean() / (neg_ret.std() + 1e-9)) * np.sqrt(252) if len(neg_ret) > 1 else 0.0

    # Max drawdown
    running_max = np.maximum.accumulate(eq_arr)
    drawdowns   = (eq_arr - running_max) / running_max
    max_dd      = float(drawdowns.min()) * 100  # en %

    calmar = total_return / (abs(max_dd) / 100 + 1e-9)

    # Breakdown par année
    yearly: Dict[str, List[float]] = {}
    for t in trades:
        y = t.get("year", "unknown")
        yearly.setdefault(y, []).append(t["net_pnl"])

    yearly_pf: Dict[str, float] = {}
    for y, pnl_list in yearly.items():
        arr = np.array(pnl_list)
        w   = arr[arr > 0].sum()
        l   = abs(arr[arr < 0].sum())
        yearly_pf[y] = round(w / l, 3) if l > 1e-9 else float("inf")

    # Benchmark buy-and-hold
    bh_return = (float(df["close"].iloc[-1]) - float(df["close"].iloc[0])) / float(df["close"].iloc[0])

    # Gate déploiement
    deployable = (
        n >= MIN_LONG_TRADES_FOR_DEPLOY
        and profit_factor >= MIN_PROFIT_FACTOR
        and expectancy > MIN_EXPECTANCY
        and abs(max_dd) <= MAX_DRAWDOWN_PCT
        and (not yearly_pf or min(yearly_pf.values()) >= MIN_YEARLY_PROFIT_FACTOR)
    )

    if n < MIN_LONG_TRADES_FOR_DEPLOY:
        status = "promising_but_insufficient_sample"
        reason = f"only {n} trades, minimum required {MIN_LONG_TRADES_FOR_DEPLOY}"
    elif not deployable:
        status = "backtest_failed_validation"
        reasons = []
        if profit_factor < MIN_PROFIT_FACTOR:
            reasons.append(f"PF={profit_factor:.2f} < {MIN_PROFIT_FACTOR}")
        if expectancy <= MIN_EXPECTANCY:
            reasons.append(f"expectancy={expectancy:.2f} <= 0")
        if abs(max_dd) > MAX_DRAWDOWN_PCT:
            reasons.append(f"max_dd={abs(max_dd):.1f}% > {MAX_DRAWDOWN_PCT}%")
        reason = "; ".join(reasons) if reasons else "unknown"
    else:
        status = "deployable"
        reason = "all validation gates passed"

    return {
        "status":          status,
        "deployable":      deployable,
        "reason":          reason,
        "n_trades":        n,
        "win_rate":        round(win_rate, 4),
        "profit_factor":   round(profit_factor, 3),
        "expectancy":      round(expectancy, 4),
        "gross_profit":    round(gross_profit, 2),
        "gross_loss":      round(gross_loss, 2),
        "total_fees_usd":  round(total_fees, 2),
        "total_return_pct": round(total_return * 100, 2),
        "final_equity":    round(final_equity, 2),
        "sharpe":          round(sharpe, 3),
        "sortino":         round(sortino, 3),
        "calmar":          round(calmar, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "yearly_profit_factor": yearly_pf,
        "benchmark_bh_pct": round(bh_return * 100, 2),
        "fees_pct":        f"{MAKER_FEE*100:.3f}% maker / {TAKER_FEE*100:.2f}% taker",
        "slippage_pct":    f"{SLIPPAGE*100:.2f}%",
        "trades":          trades,
    }


# ── Affichage ─────────────────────────────────────────────────────────────────

def print_report(m: Dict[str, Any]) -> None:
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"BACKTEST LONG-ONLY — RÉSULTATS")
    print(sep)
    print(f"Status          : {m['status']}")
    print(f"Déployable      : {'✓ OUI' if m['deployable'] else '✗ NON'}")
    if not m['deployable']:
        print(f"Raison          : {m.get('reason', '')}")
    print(sep)
    if m.get("n_trades", 0) == 0:
        print("Aucun trade généré.")
        return

    print(f"Trades          : {m['n_trades']:>9,}")
    print(f"Win rate        : {m['win_rate']*100:>8.1f}%")
    print(f"Profit Factor   : {m['profit_factor']:>9.3f}")
    print(f"Expectancy/trade: {m['expectancy']:>+9.4f} USD")
    print(f"Gross Profit    : ${m['gross_profit']:>,.2f}")
    print(f"Gross Loss      : ${m['gross_loss']:>,.2f}")
    print(f"Frais totaux    : ${m['total_fees_usd']:>,.2f}")
    print(f"Retour total    : {m['total_return_pct']:>+8.2f}%")
    print(f"Benchmark B&H   : {m['benchmark_bh_pct']:>+8.2f}%")
    print(sep)
    print(f"Sharpe          : {m['sharpe']:>9.3f}")
    print(f"Sortino         : {m['sortino']:>9.3f}")
    print(f"Calmar          : {m['calmar']:>9.3f}")
    print(f"Max Drawdown    : {m['max_drawdown_pct']:>8.2f}%")
    print(sep)
    print(f"Frais           : {m['fees_pct']}")
    print(f"Slippage        : {m['slippage_pct']}")
    if m.get("yearly_profit_factor"):
        print(sep)
        print("Breakdown par année :")
        for y, pf in sorted(m["yearly_profit_factor"].items()):
            flag = "OK" if pf >= MIN_YEARLY_PROFIT_FACTOR else "FAIL"
            print(f"  {y}  PF={pf:.3f}  [{flag}]")

    if m['n_trades'] < MIN_LONG_TRADES_FOR_DEPLOY:
        print(sep)
        print(f"⚠  AVERTISSEMENT : seulement {m['n_trades']} trades.")
        print(f"   Métriques (Sharpe, PF) peu fiables. Minimum requis : {MIN_LONG_TRADES_FOR_DEPLOY}.")
    print(sep)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest LONG-only réaliste")
    parser.add_argument("--since",    default=None,   help="Date de début ISO (ex: 2022-01-01)")
    parser.add_argument("--out",      default=None,   help="Fichier JSON de sortie")
    parser.add_argument("--run-dir",  default=None,   help="Dossier run avec signaux pré-calculés")
    parser.add_argument("--equity",   default=10000,  type=float, help="Capital initial (USD)")
    parser.add_argument("--stop",     default=0.015,  type=float, help="Stop loss fraction (défaut 1.5%%)")
    parser.add_argument("--tp",       default=0.025,  type=float, help="Take profit fraction (défaut 2.5%%)")
    parser.add_argument("--signal-col", default="long_signal", help="Colonne signal dans les données")
    args = parser.parse_args()

    if not LONG_ONLY_ENABLED:
        print("LONG_ONLY_ENABLED=False dans config/strategy_flags.py — arrêt.")
        sys.exit(1)

    run_dir = Path(args.run_dir) if args.run_dir else None

    # Essaie d'abord de charger depuis le dernier run pipeline
    if run_dir is None:
        pipeline_runs = sorted(
            (ROOT / "runs" / "pipeline").glob("*/pipeline_summary.json"),
            reverse=True,
        )
        if pipeline_runs:
            run_dir = pipeline_runs[0].parent
            log.info(f"Run détecté : {run_dir.name}")

    df = load_data(run_dir, args.since)

    if args.signal_col not in df.columns:
        log.warning(
            f"Colonne '{args.signal_col}' absente. "
            f"Colonnes disponibles : {list(df.columns[:20])}"
        )
        log.warning("Le backtest nécessite des signaux pré-calculés dans les données.")
        log.warning("Lancez train_pipeline.py d'abord pour générer les signaux.")
        result = {
            "status":     "no_signal_column",
            "deployable": False,
            "reason":     f"column '{args.signal_col}' not found in data",
            "n_trades":   0,
        }
    else:
        result = run_backtest(
            df,
            signal_col=args.signal_col,
            p_long_col="p_long",
            stop_pct=args.stop,
            tp_pct=args.tp,
            equity0=args.equity,
        )

    print_report(result)

    # Retire les trades du JSON de sortie si trop volumineux
    out_data = {k: v for k, v in result.items() if k != "trades"}
    out_data["n_trades"] = result.get("n_trades", 0)

    if args.out:
        Path(args.out).write_text(json.dumps(out_data, indent=2))
        log.info(f"Résultats exportés → {args.out}")
    else:
        print("\nJSON summary:")
        print(json.dumps(out_data, indent=2))


if __name__ == "__main__":
    main()
