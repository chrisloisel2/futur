"""
backtest/metrics.py — MÉTRIQUES DE BACKTEST
============================================

Règles strictes :
  - Toutes les métriques se calculent à partir de la liste des trades
  - Pas de regard sur la courbe de prix nue (biais)
  - Le cost_pct est appliqué systématiquement
  - Le Sharpe est annualisé sur base 24h * 365 (crypto)
  - Pas de métriques en dehors du périmètre validé

BacktestResult est le type de retour canonique pour tous les backtests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class BacktestResult:
    """
    Résultat complet d'un backtest pour un côté (long, short, ou combined).

    Tous les PnL sont en dollars absolus (capital initial = 10_000 par défaut).
    Tous les ratios sont dimensionnless.
    """

    side: str                              # "long", "short", "combined"
    n_trades: int = 0
    n_wins: int   = 0
    n_losses: int = 0

    total_pnl: float      = 0.0           # PnL net de frais en $
    total_pnl_pct: float  = 0.0           # total_pnl / capital_initial
    win_rate: float       = 0.0           # n_wins / n_trades
    profit_factor: float  = 0.0           # gross_win / gross_loss

    max_drawdown: float       = 0.0       # MDD en $ depuis pic
    max_drawdown_pct: float   = 0.0       # MDD en % du capital initial
    max_consec_losses: int    = 0

    sharpe_ratio: float   = 0.0           # Sharpe annualisé (crypto 24/365)
    calmar_ratio: float   = 0.0           # total_pnl / max_drawdown (si MDD > 0)
    expectancy: float     = 0.0           # PnL moyen par trade en $

    equity_curve: List[float] = field(default_factory=list)
    trade_list:   List[Dict]  = field(default_factory=list)

    # Méta
    cost_pct_used: float  = 0.0
    initial_equity: float = 10_000.0

    def to_dict(self) -> Dict:
        """Sérialise en dict (sans equity_curve volumineuse ni trade_list)."""
        return {
            "side":              self.side,
            "n_trades":          self.n_trades,
            "n_wins":            self.n_wins,
            "n_losses":          self.n_losses,
            "total_pnl":         round(self.total_pnl, 2),
            "total_pnl_pct":     round(self.total_pnl_pct, 4),
            "win_rate":          round(self.win_rate, 4),
            "profit_factor":     round(self.profit_factor, 4),
            "max_drawdown":      round(self.max_drawdown, 2),
            "max_drawdown_pct":  round(self.max_drawdown_pct, 4),
            "max_consec_losses": self.max_consec_losses,
            "sharpe_ratio":      round(self.sharpe_ratio, 4),
            "calmar_ratio":      round(self.calmar_ratio, 4),
            "expectancy":        round(self.expectancy, 4),
            "cost_pct_used":     self.cost_pct_used,
            "initial_equity":    self.initial_equity,
        }


def compute_backtest_metrics(
    trade_rets: np.ndarray,
    cost_pct: float,
    initial_equity: float,
    side: str,
    trade_list: Optional[List[Dict]] = None,
    bars_per_year: int = 8760,           # 24h * 365 pour 1h bars
) -> BacktestResult:
    """
    Calcule toutes les métriques à partir des returns nets par trade.

    Arguments
    ---------
    trade_rets     : array de returns nets (déjà moins cost_pct si applicable)
    cost_pct       : coût aller-retour (pour traçabilité)
    initial_equity : capital de départ
    side           : "long", "short", ou "combined"
    trade_list     : liste de dicts {bar, direction, ret, pnl, ...} optionnelle
    bars_per_year  : pour annualiser le Sharpe (8760 pour 1h bars crypto)

    Retourne
    --------
    BacktestResult complet
    """
    result = BacktestResult(
        side=side,
        cost_pct_used=cost_pct,
        initial_equity=initial_equity,
    )

    if len(trade_rets) == 0:
        return result

    n  = len(trade_rets)
    wins   = trade_rets > 0
    losses = trade_rets < 0

    result.n_trades  = n
    result.n_wins    = int(wins.sum())
    result.n_losses  = int(losses.sum())
    result.win_rate  = result.n_wins / max(n, 1)

    gross_w = float(trade_rets[wins].sum())
    gross_l = float(abs(trade_rets[losses].sum()))
    result.profit_factor = gross_w / max(gross_l, 1e-9)

    # PnL en dollars
    total_ret = float(trade_rets.sum())
    result.total_pnl     = total_ret * initial_equity
    result.total_pnl_pct = total_ret
    result.expectancy    = total_ret / max(n, 1) * initial_equity

    # Courbe d'équité
    equity = [initial_equity]
    for r in trade_rets:
        equity.append(equity[-1] * (1.0 + r))
    result.equity_curve = equity

    # Max drawdown
    eq_arr = np.array(equity)
    peak   = np.maximum.accumulate(eq_arr)
    dd     = (eq_arr - peak) / peak
    mdd_pct = float(dd.min())
    result.max_drawdown_pct = mdd_pct
    result.max_drawdown     = mdd_pct * initial_equity

    # Calmar
    if mdd_pct < 0:
        result.calmar_ratio = total_ret / abs(mdd_pct)

    # Pertes consécutives maximales
    max_cl = 0
    cur_cl = 0
    for r in trade_rets:
        if r < 0:
            cur_cl += 1
            max_cl  = max(max_cl, cur_cl)
        else:
            cur_cl = 0
    result.max_consec_losses = max_cl

    # Sharpe annualisé (sur base des returns par trade)
    # Approximation : on suppose 1 trade ≈ 1 bar (1h)
    if trade_rets.std() > 1e-9:
        sr = (trade_rets.mean() / trade_rets.std()) * np.sqrt(bars_per_year)
        result.sharpe_ratio = float(sr)

    if trade_list is not None:
        result.trade_list = trade_list

    return result


def print_backtest_summary(result: BacktestResult) -> None:
    """Affiche un résumé lisible du backtest."""
    side = result.side.upper()
    print(f"\n   ── Backtest {side} ──────────────────────────────────────────")
    print(f"   Trades : {result.n_trades}  "
          f"WR={result.win_rate:.1%}  "
          f"PF={result.profit_factor:.2f}  "
          f"E[PnL]={result.expectancy:.1f}$")
    print(f"   PnL total : {result.total_pnl:+.0f}$  ({result.total_pnl_pct:+.2%})")
    print(f"   Max DD    : {result.max_drawdown_pct:.1%}  "
          f"({result.max_drawdown:.0f}$)  "
          f"Max CL={result.max_consec_losses}")
    print(f"   Sharpe : {result.sharpe_ratio:.2f}  "
          f"Calmar={result.calmar_ratio:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# ShortRobustnessReport — rapport walk-forward impitoyable
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShortRobustnessReport:
    """
    Rapport de robustesse walk-forward du moteur short.
    Toutes les métriques clés pour décider si le short est déployable.
    """
    n_folds:            int    = 0
    wf_pf_by_fold:      List[float] = field(default_factory=list)
    wf_wr_by_fold:      List[float] = field(default_factory=list)
    wf_trades_by_fold:  List[int]   = field(default_factory=list)

    # Métriques synthétiques
    wf_pf_mean:          float = 0.0
    wf_pf_min:           float = 0.0    # pire fold
    wf_pf_consistency:   float = 0.0    # % folds avec PF > 1.0
    wf_wr_mean:          float = 0.0

    # Comparaison contre baselines
    vs_random_pf_ratio:      float = 0.0   # PF modèle / PF random signal
    vs_rsi_baseline_pf_ratio: float = 0.0  # PF modèle / PF RSI>70

    # Sensibilité aux coûts
    cost_sensitivity_break:  float = 0.0   # coût multiple où PF < 1.0

    # Pureté du régime
    regime_purity:  float = 0.0    # % trades en SHORTABLE (vs NEUTRAL/NO_SHORT)

    # Décision finale
    deploy_short:   bool  = False
    reject_reason:  str   = ""

    def to_dict(self) -> Dict:
        return {
            "n_folds":                  self.n_folds,
            "wf_pf_by_fold":            self.wf_pf_by_fold,
            "wf_wr_by_fold":            self.wf_wr_by_fold,
            "wf_trades_by_fold":        self.wf_trades_by_fold,
            "wf_pf_mean":               round(self.wf_pf_mean, 3),
            "wf_pf_min":                round(self.wf_pf_min, 3),
            "wf_pf_consistency":        round(self.wf_pf_consistency, 3),
            "wf_wr_mean":               round(self.wf_wr_mean, 3),
            "vs_random_pf_ratio":       round(self.vs_random_pf_ratio, 3),
            "vs_rsi_baseline_pf_ratio": round(self.vs_rsi_baseline_pf_ratio, 3),
            "cost_sensitivity_break":   round(self.cost_sensitivity_break, 2),
            "regime_purity":            round(self.regime_purity, 3),
            "deploy_short":             self.deploy_short,
            "reject_reason":            self.reject_reason,
        }

    def print_report(self) -> None:
        """Affiche le rapport walk-forward complet."""
        print(f"\n   ══ Walk-Forward Short — Rapport de Robustesse ══")
        print(f"   Folds     : {self.n_folds}  "
              f"trades_total={sum(self.wf_trades_by_fold)}")
        for i, (pf, wr, n) in enumerate(
            zip(self.wf_pf_by_fold, self.wf_wr_by_fold, self.wf_trades_by_fold)
        ):
            ok = "✓" if pf >= 1.0 else "✗"
            print(f"   Fold {i+1:2d}  {ok}  PF={pf:.2f}  WR={wr:.1%}  n={n}")
        print(f"   ── Synthèse ──────────────────────────────────────────")
        print(f"   PF moyen={self.wf_pf_mean:.2f}  "
              f"PF min={self.wf_pf_min:.2f}  "
              f"Consistency={self.wf_pf_consistency:.0%}")
        print(f"   vs random={self.vs_random_pf_ratio:.2f}x  "
              f"vs RSI70={self.vs_rsi_baseline_pf_ratio:.2f}x")
        print(f"   Cost break={self.cost_sensitivity_break:.1f}x  "
              f"Régime pureté={self.regime_purity:.0%}")
        verdict = "DÉPLOYABLE ✓" if self.deploy_short else f"REJETÉ ✗  ({self.reject_reason})"
        print(f"   ══ VERDICT : {verdict} ══")


def should_deploy_short(report: ShortRobustnessReport) -> Tuple[bool, str]:
    """
    Gate de déploiement du short. Binaire — pas de "short avec prudence".

    Critères (tous doivent être satisfaits) :
      1. wf_pf_consistency >= 0.67 (≥ 2/3 des folds profitables)
      2. wf_pf_min >= 0.85 (pire fold acceptable)
      3. cost_sensitivity_break >= 2.5x (résiste au coût réel)
      4. vs_random_pf_ratio >= 1.15 (bat le signal aléatoire)
      5. wf_trades_by_fold : minimum 5 trades par fold (pas de modèle fantôme)
    """
    min_trades = min(report.wf_trades_by_fold) if report.wf_trades_by_fold else 0

    checks = [
        (report.wf_pf_consistency >= 0.67,
         f"consistency={report.wf_pf_consistency:.0%} < 67%"),
        (report.wf_pf_min >= 0.85,
         f"worst_fold_PF={report.wf_pf_min:.2f} < 0.85"),
        (report.cost_sensitivity_break >= 2.5,
         f"cost_break={report.cost_sensitivity_break:.1f}x < 2.5x"),
        (report.vs_random_pf_ratio >= 1.15,
         f"vs_random={report.vs_random_pf_ratio:.2f}x < 1.15x (no alpha)"),
        (min_trades >= 5,
         f"min_trades_per_fold={min_trades} < 5 (trop rare)"),
    ]

    for ok, reason in checks:
        if not ok:
            return False, reason

    return True, "short cleared for deployment"
