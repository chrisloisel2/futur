"""
scripts/validation_engine.py — MOTEUR DE VALIDATION LONG-ONLY
=============================================================

Module central partagé par tous les scripts de validation.
Charge les modèles pkl, génère les signaux, exécute le backtest.

Ne pas exécuter directement — importer depuis les autres scripts.
"""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_ALPHA = ROOT / "data" / "BTCUSD_1h_alpha.csv"

log = logging.getLogger("validation_engine")

# ── Constantes coûts ──────────────────────────────────────────────────────────
MAKER_FEE        = 0.0005   # 0.05%
TAKER_FEE        = 0.0010   # 0.10%
SLIPPAGE         = 0.0002   # 0.02%
FUNDING_RATE_8H  = 0.0001   # 0.01% par période 8h
RISK_FREE_ANNUAL = 0.05


# ── Chargement des données ────────────────────────────────────────────────────

def load_alpha_data(
    symbol_path: Optional[Path] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> pd.DataFrame:
    """Charge le CSV alpha avec toutes les features (colonne datetime UTC)."""
    path = symbol_path or DATA_ALPHA
    df = pd.read_csv(path, parse_dates=["datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)

    if since:
        df = df[df["datetime"] >= pd.Timestamp(since, tz="UTC")]
    if until:
        df = df[df["datetime"] <= pd.Timestamp(until, tz="UTC")]

    # Normalise la colonne close
    if "Close" in df.columns and "close" not in df.columns:
        df["close"] = df["Close"]
    if "High" in df.columns and "high" not in df.columns:
        df["high"] = df["High"]
    if "Low" in df.columns and "low" not in df.columns:
        df["low"] = df["Low"]
    if "Open" in df.columns and "open" not in df.columns:
        df["open"] = df["Open"]

    return df


# ── Chargement des modèles ────────────────────────────────────────────────────

@dataclass
class LongOnlyModels:
    run_dir:        Path
    filter_model:   Any
    filter_scaler:  Any
    filter_features: List[str]
    filter_threshold: float
    edge_model:     Any
    edge_scaler:    Any
    edge_features:  List[str]
    edge_threshold: float


def load_models(run_dir: Optional[Path] = None) -> LongOnlyModels:
    """Charge filter + edge_long depuis un run directory."""
    if run_dir is None:
        # Dernier run pipeline disponible
        candidates = sorted(
            (ROOT / "runs" / "pipeline").glob("*/pipeline_summary.json"),
            reverse=True,
        )
        if not candidates:
            raise RuntimeError("Aucun run pipeline trouvé dans runs/pipeline/")
        run_dir = candidates[0].parent
    run_dir = Path(run_dir)
    log.info(f"Chargement des modèles depuis {run_dir.name}")

    def _load_pkl(path: Path):
        with open(path, "rb") as f:
            return pickle.load(f)

    def _load_meta(path: Path) -> dict:
        with open(path) as f:
            return json.load(f)

    filt_dir = run_dir / "filter"
    edge_dir = run_dir / "edge_long"

    filt_meta = _load_meta(filt_dir / "metadata.json")
    edge_meta = _load_meta(edge_dir / "metadata.json")

    filt_model = _load_pkl(filt_dir / "model.pkl")
    filt_scaler = _load_pkl(filt_dir / "scaler.pkl")

    edge_model_path = edge_dir / "model.pkl"
    edge_model  = _load_pkl(edge_model_path)
    edge_scaler = _load_pkl(edge_dir / "scaler.pkl")

    return LongOnlyModels(
        run_dir          = run_dir,
        filter_model     = filt_model,
        filter_scaler    = filt_scaler,
        filter_features  = filt_meta["features"],
        filter_threshold = filt_meta.get("calibrated_threshold_long",
                                         filt_meta.get("threshold_long", 0.51)),
        edge_model       = edge_model,
        edge_scaler      = edge_scaler,
        edge_features    = edge_meta["features"],
        edge_threshold   = edge_meta.get("threshold", 0.58),
    )


# ── Génération de signaux ─────────────────────────────────────────────────────

def _safe_transform(scaler, X: pd.DataFrame) -> np.ndarray:
    """Applique le scaler en gérant les colonnes manquantes (remplies par 0)."""
    if hasattr(scaler, "feature_names_in_"):
        needed = list(scaler.feature_names_in_)
        for col in needed:
            if col not in X.columns:
                X = X.copy()
                X[col] = 0.0
        return scaler.transform(X[needed])
    return scaler.transform(X)


def generate_signals(
    df: pd.DataFrame,
    models: LongOnlyModels,
    filter_threshold: Optional[float] = None,
    edge_threshold: Optional[float] = None,
    uncertainty_width_threshold: float = 0.30,
) -> pd.DataFrame:
    """
    Génère les signaux LONG-only sur df.

    Ajoute les colonnes :
      p_filter, filter_passed, p_long, long_signal
      rej_tradeable, rej_direction, rej_uncertainty, signal_count
    """
    df = df.copy()
    ft  = filter_threshold  if filter_threshold  is not None else models.filter_threshold
    et  = edge_threshold    if edge_threshold    is not None else models.edge_threshold

    n = len(df)
    p_filter = np.zeros(n)
    p_long   = np.zeros(n)

    # ── Level 0 : filter ─────────────────────────────────────────────────────
    feat_f = models.filter_features
    available_f = [c for c in feat_f if c in df.columns]
    Xf = df[available_f].copy()
    for col in feat_f:
        if col not in Xf.columns:
            Xf[col] = 0.0
    Xf = Xf[feat_f].fillna(0.0)
    Xfs = _safe_transform(models.filter_scaler, Xf)
    p_filter = models.filter_model.predict_proba(Xfs)[:, 1]

    # ── Level 2 : edge long ───────────────────────────────────────────────────
    feat_e = models.edge_features
    Xe = df[[c for c in feat_e if c in df.columns]].copy()
    for col in feat_e:
        if col not in Xe.columns:
            Xe[col] = 0.0
    Xe = Xe[feat_e].fillna(0.0)
    Xes = _safe_transform(models.edge_scaler, Xe)
    p_long = models.edge_model.predict_proba(Xes)[:, 1]

    df["p_filter"] = p_filter
    df["p_long"]   = p_long

    # ── Flags de rejet ────────────────────────────────────────────────────────
    df["filter_passed"] = p_filter >= ft
    df["rej_tradeable"] = p_filter < ft
    df["rej_direction"] = df["filter_passed"] & (p_long < et)

    # Uncertainty gate (approximation sur rv_24)
    rv24 = df.get("rv_24", pd.Series(np.full(n, 0.03)))
    width_approx = (rv24 * 6.0).clip(0, 1)
    df["uncertainty_width"] = width_approx
    df["rej_uncertainty"]   = df["filter_passed"] & (p_long >= et) & (width_approx > uncertainty_width_threshold)

    # Signal final
    df["long_signal"] = (
        df["filter_passed"]
        & (p_long >= et)
        & ~df["rej_uncertainty"]
    )

    return df


# ── Backtest core ─────────────────────────────────────────────────────────────

@dataclass
class BacktestParams:
    stop_pct:        float = 0.015
    tp_pct:          float = 0.025
    equity0:         float = 10_000.0
    position_size_pct: float = 0.02
    cooldown_bars:   int   = 2
    max_consecutive_losses: int = 3
    daily_loss_limit_pct: float = 0.02


def run_backtest_core(
    df: pd.DataFrame,
    params: Optional[BacktestParams] = None,
) -> Dict[str, Any]:
    """
    Backtest LONG-only à partir d'un df avec colonne long_signal.
    Inclut frais maker/taker, slippage, market impact, funding cost.
    Retourne les métriques complètes + gate rejection report.
    """
    if params is None:
        params = BacktestParams()

    # Gate rejection counts (sur toutes les barres)
    total_bars          = len(df)
    rej_tradeable       = int(df.get("rej_tradeable", pd.Series(False, index=df.index)).sum())
    rej_direction       = int(df.get("rej_direction", pd.Series(False, index=df.index)).sum())
    rej_uncertainty     = int(df.get("rej_uncertainty", pd.Series(False, index=df.index)).sum())
    # rej_risk et rej_regime comptés pendant la boucle
    rej_risk            = 0
    rej_regime          = 0
    accepted_setups     = int(df.get("long_signal", pd.Series(False, index=df.index)).sum())

    equity = params.equity0
    equity_curve: List[float] = [equity]
    trades: List[Dict] = []
    in_trade = False
    entry_bar  = -999
    last_exit_bar = -999
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    position_notional = 0.0
    consecutive_losses = 0
    day_pnl = 0.0
    day_start_equity = params.equity0
    last_day = ""

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        price   = float(row.get("close", row.get("Close", 0)))
        high    = float(row.get("high",  row.get("High", price)))
        low     = float(row.get("low",   row.get("Low",  price)))
        volume  = float(row.get("Volume", row.get("volume", 0)))
        ts      = row.get("datetime", None)

        # Reset journalier (consecutive_losses + day_pnl, comme la production)
        day_str = str(ts.date()) if hasattr(ts, "date") else str(i)[:10]
        if day_str != last_day:
            day_pnl = 0.0
            day_start_equity = equity
            consecutive_losses = 0
            last_day = day_str

        vol_notional = price * volume if volume > 0 else 1e6

        if in_trade:
            if low <= stop_price:
                exit_price  = stop_price
                exit_reason = "stop"
            elif high >= tp_price:
                exit_price  = tp_price
                exit_reason = "tp"
            else:
                equity_curve.append(equity)
                continue

            bars_held = i - entry_bar
            pnl_raw   = (exit_price - entry_price) / entry_price * position_notional
            fee_entry = position_notional * (MAKER_FEE + SLIPPAGE)
            fee_exit  = position_notional * (TAKER_FEE + SLIPPAGE)
            funding   = position_notional * FUNDING_RATE_8H * bars_held / 8.0

            rv24     = float(prev.get("rv_24", 0.02)) if hasattr(prev, "get") else 0.02
            vol_bps  = rv24 * 10_000
            mi_bps   = _market_impact_bps(position_notional, vol_notional, vol_bps)
            mi_cost  = position_notional * mi_bps / 10_000

            net_pnl = pnl_raw - fee_entry - fee_exit - funding - mi_cost
            equity += net_pnl
            day_pnl += net_pnl

            if net_pnl < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            year = str(ts.year) if hasattr(ts, "year") else "?"
            trades.append({
                "entry_bar":     entry_bar,
                "exit_bar":      i,
                "bars_held":     bars_held,
                "entry_price":   round(entry_price, 2),
                "exit_price":    round(exit_price, 2),
                "exit_reason":   exit_reason,
                "pnl_raw":       round(pnl_raw, 4),
                "fees":          round(fee_entry + fee_exit, 4),
                "funding":       round(funding, 4),
                "market_impact": round(mi_cost, 4),
                "net_pnl":       round(net_pnl, 4),
                "equity":        round(equity, 2),
                "year":          year,
            })
            equity_curve.append(equity)
            last_exit_bar = i
            in_trade = False

        else:
            signal = bool(prev.get("long_signal", False))
            if signal:
                # Kill-switches risque (cooldown depuis la dernière SORTIE)
                cooldown_ok = (i - last_exit_bar) >= params.cooldown_bars
                if not cooldown_ok:
                    rej_risk += 1
                    equity_curve.append(equity)
                    continue
                if consecutive_losses >= params.max_consecutive_losses:
                    rej_risk += 1
                    equity_curve.append(equity)
                    continue
                if day_pnl <= -params.daily_loss_limit_pct * day_start_equity:
                    rej_risk += 1
                    equity_curve.append(equity)
                    continue

                entry_price       = price * (1 + SLIPPAGE)
                position_notional = equity * params.position_size_pct
                stop_price        = entry_price * (1 - params.stop_pct)
                tp_price          = entry_price * (1 + params.tp_pct)
                entry_bar         = i
                in_trade          = True

            equity_curve.append(equity)

    gate_report = {
        "total_bars":          total_bars,
        "rejected_by_tradeable_filter": rej_tradeable,
        "rejected_by_direction_threshold": rej_direction,
        "rejected_by_uncertainty_gate":    rej_uncertainty,
        "rejected_by_risk_gate":           rej_risk,
        "rejected_by_regime_gate":         rej_regime,
        "accepted_trade_setups":           accepted_setups,
        "executed_trades":                 len(trades),
    }

    metrics = _compute_metrics(trades, equity_curve, params.equity0, df)
    metrics["gate_report"] = gate_report
    return metrics


def _market_impact_bps(order_notional, vol_notional, vol_bps, k=0.15):
    if vol_notional <= 0:
        return 0.0
    participation = order_notional / vol_notional
    return k * (participation ** 0.5) * vol_bps


def _compute_metrics(
    trades: List[Dict],
    equity_curve: List[float],
    equity0: float,
    df: pd.DataFrame,
) -> Dict[str, Any]:
    from config.strategy_flags import (
        MIN_LONG_TRADES_FOR_DEPLOY, MIN_PROFIT_FACTOR,
        MIN_EXPECTANCY, MAX_DRAWDOWN_PCT, MIN_YEARLY_PROFIT_FACTOR,
    )

    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0, "deployable": False,
            "reason": "no_trades", "status": "no_signal",
        }

    pnls      = np.array([t["net_pnl"] for t in trades])
    wins      = pnls[pnls > 0]
    losses    = pnls[pnls < 0]
    gross_p   = float(wins.sum())   if len(wins)   > 0 else 0.0
    gross_l   = float(losses.sum()) if len(losses) > 0 else 0.0
    pf        = gross_p / abs(gross_l) if abs(gross_l) > 1e-9 else float("inf")
    win_rate  = len(wins) / n
    expectancy = float(pnls.mean())
    total_fees = sum(t["fees"] + t["funding"] + t["market_impact"] for t in trades)

    eq_arr       = np.array(equity_curve, dtype=float)
    final_equity = float(eq_arr[-1])
    total_return = (final_equity - equity0) / equity0

    daily_eq  = eq_arr[::24]
    daily_ret = np.diff(daily_eq) / (daily_eq[:-1] + 1e-9)
    rf_daily  = RISK_FREE_ANNUAL / 252
    excess    = daily_ret - rf_daily
    sharpe    = float((excess.mean() / (excess.std() + 1e-9)) * np.sqrt(252)) if len(excess) > 1 else 0.0
    neg_ret   = daily_ret[daily_ret < 0]
    sortino   = float((excess.mean() / (neg_ret.std() + 1e-9)) * np.sqrt(252)) if len(neg_ret) > 1 else 0.0

    run_max   = np.maximum.accumulate(eq_arr)
    drawdowns = (eq_arr - run_max) / (run_max + 1e-9)
    max_dd    = float(drawdowns.min()) * 100

    calmar = total_return / (abs(max_dd) / 100 + 1e-9)

    yearly: Dict[str, List[float]] = {}
    for t in trades:
        y = t.get("year", "?")
        yearly.setdefault(y, []).append(t["net_pnl"])

    yearly_pf: Dict[str, float] = {}
    for y, pl in yearly.items():
        arr = np.array(pl)
        w = arr[arr > 0].sum(); l = abs(arr[arr < 0].sum())
        yearly_pf[y] = round(w / l, 3) if l > 1e-9 else float("inf")

    # Exposition (fraction du temps en position)
    in_trade_bars = sum(t["bars_held"] for t in trades)
    exposure_pct  = in_trade_bars / len(df) * 100 if len(df) > 0 else 0.0

    # Turnover
    turnover = sum(t.get("fees", 0) for t in trades) / equity0

    # Benchmark B&H
    close_col = "close" if "close" in df.columns else "Close"
    bh_return = 0.0
    if close_col in df.columns:
        prices = df[close_col].dropna()
        if len(prices) > 1:
            bh_return = (float(prices.iloc[-1]) - float(prices.iloc[0])) / float(prices.iloc[0])

    # Deployable gate
    deployable = (
        n >= MIN_LONG_TRADES_FOR_DEPLOY
        and pf >= MIN_PROFIT_FACTOR
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
        if pf < MIN_PROFIT_FACTOR:
            reasons.append(f"PF={pf:.2f} < {MIN_PROFIT_FACTOR}")
        if expectancy <= MIN_EXPECTANCY:
            reasons.append(f"expectancy={expectancy:.4f} <= 0")
        if abs(max_dd) > MAX_DRAWDOWN_PCT:
            reasons.append(f"max_dd={abs(max_dd):.1f}% > {MAX_DRAWDOWN_PCT}%")
        if yearly_pf and min(yearly_pf.values()) < MIN_YEARLY_PROFIT_FACTOR:
            bad = {y: v for y, v in yearly_pf.items() if v < MIN_YEARLY_PROFIT_FACTOR}
            reasons.append(f"yearly_pf_fail={bad}")
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
        "profit_factor":   round(pf, 3),
        "expectancy":      round(expectancy, 4),
        "gross_profit":    round(gross_p, 2),
        "gross_loss":      round(gross_l, 2),
        "total_fees_usd":  round(total_fees, 2),
        "total_return_pct": round(total_return * 100, 2),
        "final_equity":    round(final_equity, 2),
        "sharpe":          round(sharpe, 3),
        "sortino":         round(sortino, 3),
        "calmar":          round(calmar, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "exposure_pct":    round(exposure_pct, 2),
        "turnover":        round(turnover, 6),
        "yearly_profit_factor": yearly_pf,
        "benchmark_bh_pct": round(bh_return * 100, 2),
        "fees_pct":        f"{MAKER_FEE*100:.3f}% maker / {TAKER_FEE*100:.2f}% taker",
        "slippage_pct":    f"{SLIPPAGE*100:.2f}%",
        "trades":          trades,
    }
