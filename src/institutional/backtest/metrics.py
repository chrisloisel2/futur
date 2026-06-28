"""
src/institutional/backtest/metrics.py
─────────────────────────────────────────────────────────────────────────────
Métriques de performance institutionnelles.

Toutes les métriques prennent une equity curve comme entrée.
Aucune n'utilise les rendements bruts — toujours net de frais et slippage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


TRADING_HOURS_PER_YEAR = 24 * 365


@dataclass
class PerformanceReport:
    """Rapport de performance complet d'un backtest."""
    pf: float
    sharpe: float
    sortino: float
    calmar: float
    cagr: float
    max_drawdown: float
    max_drawdown_duration_bars: int
    hit_rate: float
    avg_win: float
    avg_loss: float
    win_loss_ratio: float
    expectancy: float
    n_trades: int
    n_wins: int
    n_losses: int
    avg_holding_bars: float
    exposure_pct: float
    turnover_annual: float
    skewness: float
    kurtosis: float
    worst_year: float
    worst_month: float
    best_year: float
    best_month: float
    annual_returns: Dict[int, float] = field(default_factory=dict)
    monthly_returns: Dict[str, float] = field(default_factory=dict)
    # Sensibilité aux coûts
    pf_cost_x2: float = 0.0
    pf_cost_x3: float = 0.0
    sharpe_cost_x2: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pf": round(self.pf, 4),
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "calmar": round(self.calmar, 4),
            "cagr": round(self.cagr, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "max_dd_duration_bars": self.max_drawdown_duration_bars,
            "hit_rate": round(self.hit_rate, 4),
            "avg_win": round(self.avg_win, 6),
            "avg_loss": round(self.avg_loss, 6),
            "win_loss_ratio": round(self.win_loss_ratio, 4),
            "expectancy": round(self.expectancy, 6),
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "avg_holding_bars": round(self.avg_holding_bars, 1),
            "exposure_pct": round(self.exposure_pct, 4),
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
            "worst_year": round(self.worst_year, 4),
            "worst_month": round(self.worst_month, 4),
            "best_year": round(self.best_year, 4),
            "best_month": round(self.best_month, 4),
            "pf_cost_x2": round(self.pf_cost_x2, 4),
            "pf_cost_x3": round(self.pf_cost_x3, 4),
            "sharpe_cost_x2": round(self.sharpe_cost_x2, 4),
        }

    def verdict(self) -> str:
        """Verdict selon les critères institutionnels."""
        if (self.pf >= 1.30
                and self.pf_cost_x2 >= 1.10
                and self.worst_year >= 0.95
                and self.n_trades >= 100
                and self.sharpe >= 0.8):
            return "PROMOTE"
        elif (self.pf >= 1.20
              and self.pf_cost_x2 >= 1.05
              and self.worst_year >= 0.90
              and self.n_trades >= 50):
            return "PAPER"
        elif (self.pf >= 1.10
              and self.n_trades >= 30):
            return "INCUBATE"
        elif self.pf >= 1.05:
            return "REJECT"
        return "REJECT"


def compute_equity_metrics(
    equity: pd.Series,
    trades_df: Optional[pd.DataFrame] = None,
    risk_free_rate: float = 0.04,
) -> PerformanceReport:
    """
    Calcule toutes les métriques depuis une equity curve horaire.

    equity  : pd.Series avec index DatetimeIndex (valeur du portefeuille en USD)
    trades_df : DataFrame des trades (colonnes: pnl_net, holding_bars, ...)
    """
    if len(equity) < 2:
        raise ValueError("Equity curve trop courte pour les métriques")

    # Rendements horaires
    ret = equity.pct_change().dropna()
    log_ret = np.log(equity / equity.shift(1)).dropna()

    # CAGR
    n_years = len(equity) / TRADING_HOURS_PER_YEAR
    total_ret = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total_ret) ** (1 / max(n_years, 0.001)) - 1

    # Sharpe (annualisé)
    rf_hourly = (1 + risk_free_rate) ** (1 / TRADING_HOURS_PER_YEAR) - 1
    excess_ret = ret - rf_hourly
    sharpe = float(excess_ret.mean() / (excess_ret.std() + 1e-9) * np.sqrt(TRADING_HOURS_PER_YEAR))

    # Sortino (downside deviation)
    downside = ret[ret < rf_hourly] - rf_hourly
    downside_std = np.sqrt((downside ** 2).mean()) + 1e-9
    sortino = float(excess_ret.mean() / downside_std * np.sqrt(TRADING_HOURS_PER_YEAR))

    # Max Drawdown
    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / (roll_max + 1e-9)
    max_dd = float(drawdown.min())

    # Durée max drawdown
    in_dd = (drawdown < 0).astype(int)
    dd_runs = (in_dd != in_dd.shift()).cumsum()
    dd_lengths = in_dd.groupby(dd_runs).transform("sum")
    max_dd_dur = int(dd_lengths.max())

    # Calmar
    calmar = float(cagr / (abs(max_dd) + 1e-9))

    # Annual / Monthly returns
    annual_ret = equity.resample("Y").last().pct_change().dropna()
    monthly_ret = equity.resample("M").last().pct_change().dropna()

    annual_dict = {
        int(ts.year): float(r) for ts, r in annual_ret.items()
    }
    monthly_dict = {
        str(ts)[:7]: float(r) for ts, r in monthly_ret.items()
    }

    worst_year = float(annual_ret.min()) if len(annual_ret) > 0 else 0.0
    best_year = float(annual_ret.max()) if len(annual_ret) > 0 else 0.0
    worst_month = float(monthly_ret.min()) if len(monthly_ret) > 0 else 0.0
    best_month = float(monthly_ret.max()) if len(monthly_ret) > 0 else 0.0

    # Skewness / Kurtosis
    skew = float(ret.skew())
    kurt = float(ret.kurtosis())

    # Trade-level metrics
    if trades_df is not None and len(trades_df) > 0:
        pnl_col = "pnl_net" if "pnl_net" in trades_df.columns else "pnl"
        pnl = trades_df[pnl_col]
        n_trades = len(pnl)
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        n_wins = len(wins)
        n_losses = len(losses)
        hit_rate = n_wins / max(n_trades, 1)
        avg_win = float(wins.mean()) if n_wins > 0 else 0.0
        avg_loss = float(losses.mean()) if n_losses > 0 else 0.0
        win_loss_ratio = abs(avg_win / (avg_loss + 1e-9))
        expectancy = float(pnl.mean())
        pf = float(wins.sum() / (-losses.sum() + 1e-9)) if n_losses > 0 else float("inf")
        avg_holding = float(trades_df.get("holding_bars", pd.Series([0])).mean())
        exposure = float(trades_df.get("exposure_bars", pd.Series([0])).sum()) / max(len(equity), 1)
    else:
        n_trades = n_wins = n_losses = 0
        hit_rate = avg_win = avg_loss = win_loss_ratio = expectancy = exposure = 0.0
        pf = 1.0
        avg_holding = 0.0

    return PerformanceReport(
        pf=pf,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        cagr=cagr,
        max_drawdown=max_dd,
        max_drawdown_duration_bars=max_dd_dur,
        hit_rate=hit_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        win_loss_ratio=win_loss_ratio,
        expectancy=expectancy,
        n_trades=n_trades,
        n_wins=n_wins,
        n_losses=n_losses,
        avg_holding_bars=avg_holding,
        exposure_pct=exposure,
        turnover_annual=float(n_trades / max(n_years, 0.001)),
        skewness=skew,
        kurtosis=kurt,
        worst_year=worst_year,
        worst_month=worst_month,
        best_year=best_year,
        best_month=best_month,
        annual_returns=annual_dict,
        monthly_returns=monthly_dict,
    )


def stress_test_costs(
    trades_df: pd.DataFrame,
    base_cost_bps: float = 10.0,
) -> Dict[str, float]:
    """
    Recalcule le PF avec des frais multipliés.
    Retourne {"pf_cost_x1": ..., "pf_cost_x2": ..., "pf_cost_x3": ...}
    """
    if "pnl_net" not in trades_df.columns or "notional" not in trades_df.columns:
        return {}

    results = {}
    for mult in [1, 2, 3]:
        extra_cost = trades_df["notional"] * (base_cost_bps / 10_000) * (mult - 1)
        pnl_adjusted = trades_df["pnl_net"] - extra_cost
        wins = pnl_adjusted[pnl_adjusted > 0].sum()
        losses = (-pnl_adjusted[pnl_adjusted <= 0]).sum()
        results[f"pf_cost_x{mult}"] = float(wins / (losses + 1e-9))

    return results
