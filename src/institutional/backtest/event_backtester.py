"""
src/institutional/backtest/event_backtester.py
─────────────────────────────────────────────────────────────────────────────
Backtester événementiel minimal mais réel.

Convention d'exécution :
  Signal détecté au close de la barre T
  Entrée au close de T + slippage (même barre, crypto liquide 24/7)
  Sortie au close de T+H + slippage (après max_holding_bars)
  Stop/TP vérifiés sur high/low de chaque barre intermédiaire

Outputs :
  orders.parquet       : ordres soumis
  fills.parquet        : fills réels (prix + frais + slippage)
  positions.parquet    : état position par barre
  equity_curve.parquet : equity curve 1h
  metrics.json         : métriques de performance complètes
  report.md            : rapport humain

Python 3.8+ compatible (utilisé avec le .venv existant).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── Config ───────────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    initial_capital:    float = 10_000.0
    position_size_pct:  float = 0.25
    signal_threshold:   float = 0.60        # valeur dépend de threshold_type
    threshold_type:     str   = "absolute"  # "absolute" | "percentile" | "relative"
    # "absolute"   : p_up > signal_threshold (ex: 0.60)
    # "percentile" : prendre top signal_threshold% des barres (ex: 0.10 = top 10%)
    # "relative"   : p_up > signal_threshold × base_rate (ex: 2.0 = 2× la prévalence)
    long_enabled:       bool  = True
    short_enabled:      bool  = False
    max_holding_bars:   int   = 24
    stop_loss_pct:      float = 0.0
    take_profit_pct:    float = 0.0
    taker_fee_bps:      float = 5.0
    slippage_bps:       float = 2.0
    cost_multiplier:    float = 1.0

    @property
    def fee_frac(self) -> float:
        return self.taker_fee_bps * self.cost_multiplier / 10_000

    @property
    def slippage_frac(self) -> float:
        return self.slippage_bps * self.cost_multiplier / 10_000

    @property
    def round_trip_cost_frac(self) -> float:
        return 2 * (self.fee_frac + self.slippage_frac)


# ─── Records ──────────────────────────────────────────────────────────────────

@dataclass
class Order:
    order_id:   int
    ts:         pd.Timestamp
    asset:      str
    side:       str          # "buy" | "sell"
    size_usd:   float
    price_ref:  float        # prix de référence (close T)
    signal_prob: float


@dataclass
class Fill:
    fill_id:    int
    order_id:   int
    ts:         pd.Timestamp
    asset:      str
    side:       str
    size_usd:   float
    price:      float        # prix d'exécution (avec slippage)
    fee:        float
    slippage_cost: float
    fill_type:  str          # "entry" | "exit"


@dataclass
class Trade:
    trade_id:     int
    asset:        str
    direction:    int         # +1 long, -1 short
    entry_ts:     pd.Timestamp
    entry_price:  float
    size_usd:     float
    size_units:   float
    entry_fee:    float
    exit_ts:      Optional[pd.Timestamp] = None
    exit_price:   Optional[float]        = None
    exit_fee:     float                  = 0.0
    exit_reason:  str                    = ""
    pnl_gross:    float                  = 0.0
    pnl_net:      float                  = 0.0
    fold_year:    Optional[int]          = None
    model_path:   Optional[str]          = None
    model_type:   Optional[str]          = None
    prediction:   Optional[float]        = None
    threshold:    Optional[float]        = None

    @property
    def is_open(self) -> bool:
        return self.exit_ts is None

    def close(
        self,
        ts:     pd.Timestamp,
        price:  float,
        fee:    float,
        reason: str,
    ) -> None:
        self.exit_ts    = ts
        self.exit_price = price
        self.exit_fee   = fee
        self.exit_reason = reason
        if self.direction == 1:
            self.pnl_gross = (price - self.entry_price) * self.size_units
        else:
            self.pnl_gross = (self.entry_price - price) * self.size_units
        self.pnl_net = self.pnl_gross - self.entry_fee - fee


# ─── Résultat ─────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    config:       BacktestConfig
    trades:       List[Trade]      = field(default_factory=list)
    orders:       List[Order]      = field(default_factory=list)
    fills:        List[Fill]       = field(default_factory=list)
    equity_bars:  List[Dict]       = field(default_factory=list)
    position_bars: List[Dict]      = field(default_factory=list)

    def equity_series(self) -> pd.Series:
        if not self.equity_bars:
            return pd.Series(dtype=float)
        df = pd.DataFrame(self.equity_bars).set_index("timestamp")
        return df["equity"]

    def n_trades(self) -> int:
        return sum(1 for t in self.trades if not t.is_open)

    def n_wins(self) -> int:
        return sum(1 for t in self.trades if not t.is_open and t.pnl_net > 0)

    def pf(self) -> float:
        wins   = sum(t.pnl_net for t in self.trades if not t.is_open and t.pnl_net > 0)
        losses = sum(-t.pnl_net for t in self.trades if not t.is_open and t.pnl_net <= 0)
        return wins / max(losses, 1e-9)

    def total_fees(self) -> float:
        return sum(f.fee for f in self.fills)

    def total_slippage(self) -> float:
        return sum(f.slippage_cost for f in self.fills)


# ─── Backtester ───────────────────────────────────────────────────────────────

class EventBacktester:
    """
    Backteste barre par barre avec simulation d'exécution.

    proba_df doit avoir :
      - même index DatetimeIndex UTC que ohlcv
      - colonnes : p_up (proba classe +1), p_down (proba classe -1), p_flat

    Usage :
        bt      = EventBacktester()
        config  = BacktestConfig(signal_threshold=0.60, max_holding_bars=24)
        result  = bt.run(ohlcv_df, proba_df, config, asset="BTCUSDT")
    """

    def run(
        self,
        ohlcv:    pd.DataFrame,
        proba_df: pd.DataFrame,
        config:   BacktestConfig,
        asset:    str = "unknown",
    ) -> BacktestResult:
        """
        Exécute le backtest barre par barre.

        ohlcv    : OHLCV avec index DatetimeIndex UTC
        proba_df : probabilités avec colonnes p_up, p_down, p_flat
        config   : configuration du backtest
        """
        result = BacktestResult(config=config)

        # Aligner sur l'intersection
        common = ohlcv.index.intersection(proba_df.index)
        ohlcv   = ohlcv.loc[common]
        proba   = proba_df.loc[common]

        # Pré-calculer les seuils effectifs selon threshold_type
        p_up_all   = proba["p_up"].values   if "p_up"   in proba.columns else np.zeros(len(proba))
        p_down_all = proba["p_down"].values if "p_down" in proba.columns else np.zeros(len(proba))

        if config.threshold_type == "percentile":
            # Seuil = percentile (1 - signal_threshold) de p_up
            # signal_threshold=0.10 → top 10% des barres
            thr_up   = float(np.quantile(p_up_all,   1 - config.signal_threshold))
            thr_down = float(np.quantile(p_down_all, 1 - config.signal_threshold))
        elif config.threshold_type == "relative":
            # Seuil = signal_threshold × moyenne(p_up)
            thr_up   = config.signal_threshold * float(np.mean(p_up_all))
            thr_down = config.signal_threshold * float(np.mean(p_down_all))
        else:
            # "absolute" (défaut)
            thr_up   = config.signal_threshold
            thr_down = config.signal_threshold

        equity   = config.initial_capital
        position: Optional[Trade] = None
        order_ctr = fill_ctr = trade_ctr = 0

        for i, ts in enumerate(ohlcv.index):
            bar = ohlcv.iloc[i]
            O, H, L, C = float(bar.get("open", bar.get("close", 0))), \
                         float(bar.get("high", bar.get("close", 0))), \
                         float(bar.get("low",  bar.get("close", 0))), \
                         float(bar["close"])

            p_up   = float(proba.loc[ts, "p_up"])   if "p_up"   in proba.columns else 0.0
            p_down = float(proba.loc[ts, "p_down"]) if "p_down" in proba.columns else 0.0

            # ── 1. Vérifier stop/TP/max_holding sur la position ouverte ──────
            if position is not None:
                bars_held = i - ohlcv.index.get_loc(position.entry_ts)

                exit_price = None
                exit_reason = None

                # Stop-loss (vérifié sur low/high intrabar)
                if config.stop_loss_pct > 0:
                    if position.direction == 1 and L <= position.entry_price * (1 - config.stop_loss_pct):
                        exit_price  = position.entry_price * (1 - config.stop_loss_pct)
                        exit_reason = "stop_loss"
                    elif position.direction == -1 and H >= position.entry_price * (1 + config.stop_loss_pct):
                        exit_price  = position.entry_price * (1 + config.stop_loss_pct)
                        exit_reason = "stop_loss"

                # Take-profit
                if exit_price is None and config.take_profit_pct > 0:
                    if position.direction == 1 and H >= position.entry_price * (1 + config.take_profit_pct):
                        exit_price  = position.entry_price * (1 + config.take_profit_pct)
                        exit_reason = "take_profit"
                    elif position.direction == -1 and L <= position.entry_price * (1 - config.take_profit_pct):
                        exit_price  = position.entry_price * (1 - config.take_profit_pct)
                        exit_reason = "take_profit"

                # Max holding
                if exit_price is None and bars_held >= config.max_holding_bars:
                    exit_price  = C
                    exit_reason = "max_holding"

                if exit_price is not None:
                    # Slippage adverse à la sortie
                    slip = config.slippage_frac
                    if position.direction == 1:
                        actual_exit = exit_price * (1 - slip)
                        exit_side   = "sell"
                    else:
                        actual_exit = exit_price * (1 + slip)
                        exit_side   = "buy"

                    exit_fee = position.size_usd * config.fee_frac
                    slip_cost_exit = abs(exit_price - actual_exit) * position.size_units

                    fill_ctr += 1
                    result.fills.append(Fill(
                        fill_id=fill_ctr, order_id=-1, ts=ts, asset=asset,
                        side=exit_side, size_usd=position.size_usd,
                        price=actual_exit, fee=exit_fee,
                        slippage_cost=slip_cost_exit, fill_type="exit",
                    ))

                    position.close(ts, actual_exit, exit_fee, exit_reason)
                    equity += position.pnl_net
                    result.trades.append(position)
                    position = None

            # ── 2. Signal d'entrée (si pas de position) ────────────────────────
            if position is None:
                direction = None
                signal_prob = 0.0

                if config.long_enabled and p_up > thr_up:
                    direction   = 1
                    signal_prob = p_up
                elif config.short_enabled and p_down > thr_down:
                    direction   = -1
                    signal_prob = p_down

                if direction is not None:
                    size_usd   = equity * config.position_size_pct
                    slip       = config.slippage_frac

                    if direction == 1:
                        entry_price = C * (1 + slip)
                        entry_side  = "buy"
                    else:
                        entry_price = C * (1 - slip)
                        entry_side  = "sell"

                    entry_fee  = size_usd * config.fee_frac
                    slip_cost_entry = abs(C - entry_price) * (size_usd / entry_price)
                    size_units = size_usd / entry_price

                    order_ctr += 1
                    result.orders.append(Order(
                        order_id=order_ctr, ts=ts, asset=asset,
                        side=entry_side, size_usd=size_usd,
                        price_ref=C, signal_prob=signal_prob,
                    ))

                    fill_ctr += 1
                    result.fills.append(Fill(
                        fill_id=fill_ctr, order_id=order_ctr, ts=ts, asset=asset,
                        side=entry_side, size_usd=size_usd,
                        price=entry_price, fee=entry_fee,
                        slippage_cost=slip_cost_entry, fill_type="entry",
                    ))

                    trade_ctr += 1
                    position = Trade(
                        trade_id=trade_ctr, asset=asset, direction=direction,
                        entry_ts=ts, entry_price=entry_price,
                        size_usd=size_usd, size_units=size_units,
                        entry_fee=entry_fee,
                    )

            # ── 3. Mark-to-market equity ────────────────────────────────────
            unrealized = 0.0
            if position is not None:
                if position.direction == 1:
                    unrealized = (C - position.entry_price) * position.size_units
                else:
                    unrealized = (position.entry_price - C) * position.size_units

            result.equity_bars.append({
                "timestamp": ts,
                "equity":    equity + unrealized,
                "cash":      equity,
                "unrealized": unrealized,
                "in_position": position is not None,
            })

            result.position_bars.append({
                "timestamp":   ts,
                "has_position": position is not None,
                "direction":   position.direction if position else 0,
                "entry_price": position.entry_price if position else np.nan,
                "current_price": C,
                "unrealized_pnl": unrealized,
            })

        # Forcer fermeture de la position restante en fin de série
        if position is not None:
            last_ts    = ohlcv.index[-1]
            last_close = float(ohlcv.iloc[-1]["close"])
            slip       = config.slippage_frac
            actual_exit = last_close * (1 - slip) if position.direction == 1 else last_close * (1 + slip)
            exit_fee    = position.size_usd * config.fee_frac
            position.close(last_ts, actual_exit, exit_fee, "end_of_data")
            equity += position.pnl_net
            result.trades.append(position)

        return result


# ─── Métriques depuis résultat ────────────────────────────────────────────────

def compute_backtest_metrics(result: BacktestResult, cost_bps_base: float = 10.0) -> Dict:
    """Calcule toutes les métriques depuis un BacktestResult."""
    trades = [t for t in result.trades if not t.is_open]
    eq     = result.equity_series()

    if len(eq) < 2:
        return {"error": "equity curve trop courte"}

    initial = result.config.initial_capital
    final   = float(eq.iloc[-1])

    # Rendements horaires
    ret = eq.pct_change().dropna()

    # Sharpe (annualisé)
    sharpe = float(ret.mean() / (ret.std() + 1e-9) * np.sqrt(8760))

    # Sortino (annualisé — downside deviation uniquement)
    downside = ret[ret < 0]
    sortino  = float(ret.mean() / (downside.std() + 1e-9) * np.sqrt(8760)) if len(downside) > 1 else 0.0

    # Max DD
    roll_max = eq.cummax()
    dd       = (eq - roll_max) / (roll_max + 1e-9)
    max_dd   = float(dd.min())

    # CAGR
    n_years = len(eq) / 8760
    cagr    = (final / initial) ** (1 / max(n_years, 0.001)) - 1

    # Trade metrics
    n_t   = len(trades)
    n_win = sum(1 for t in trades if t.pnl_net > 0)
    pnl_net_series = np.array([t.pnl_net for t in trades]) if trades else np.array([0.0])
    gross_pnl = sum(t.pnl_gross for t in trades)
    net_pnl   = sum(t.pnl_net   for t in trades)

    wins   = [t.pnl_net for t in trades if t.pnl_net > 0]
    losses = [t.pnl_net for t in trades if t.pnl_net <= 0]
    pf     = sum(wins) / max(-sum(losses), 1e-9)

    avg_win  = float(np.mean(wins))  if wins  else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0

    holding_h = [
        (t.exit_ts - t.entry_ts).total_seconds() / 3600
        for t in trades if t.exit_ts
    ]

    # Turnover : volume total / equity moyenne
    total_volume = sum(t.size_usd for t in trades) * 2  # entrée + sortie
    avg_equity   = float(eq.mean()) if len(eq) > 0 else initial
    turnover     = total_volume / max(avg_equity, 1.0)

    # PnL par année (basé sur entry_ts)
    pnl_by_year: Dict[str, float] = {}
    for t in trades:
        yr = str(t.entry_ts.year)
        pnl_by_year[yr] = round(pnl_by_year.get(yr, 0.0) + t.pnl_net, 2)

    # PnL par fold (basé sur fold_year si disponible)
    pnl_by_fold: Dict[str, float] = {}
    for t in trades:
        if t.fold_year is not None:
            key = str(t.fold_year)
            pnl_by_fold[key] = round(pnl_by_fold.get(key, 0.0) + t.pnl_net, 2)

    # PnL par modèle utilisé
    pnl_by_model: Dict[str, float] = {}
    for t in trades:
        if t.model_path is not None:
            key = t.model_path
            pnl_by_model[key] = round(pnl_by_model.get(key, 0.0) + t.pnl_net, 2)

    # Cost sensitivity
    def _pf_with_mult(mult: float) -> float:
        extra_per_trade = (sum(t.size_usd for t in trades) / max(n_t, 1)) * \
                          (cost_bps_base / 10_000) * (mult - 1)
        adj_losses = [-t.pnl_net + extra_per_trade for t in trades if t.pnl_net <= 0]
        adj_wins   = [t.pnl_net - extra_per_trade  for t in trades if t.pnl_net > 0]
        return sum(adj_wins) / max(sum(adj_losses), 1e-9)

    return {
        "initial_capital": initial,
        "final_equity":    round(final, 2),
        "pf":              round(pf, 4),
        "sharpe":          round(sharpe, 4),
        "sortino":         round(sortino, 4),
        "cagr":            round(cagr, 4),
        "max_drawdown":    round(max_dd, 4),
        "n_trades":        n_t,
        "n_wins":          n_win,
        "hit_rate":        round(n_win / max(n_t, 1), 4),
        "avg_win":         round(avg_win, 4),
        "avg_loss":        round(avg_loss, 4),
        "win_loss_ratio":  round(abs(avg_win / avg_loss) if avg_loss else 0.0, 4),
        "expectancy":      round(float(np.mean(pnl_net_series)), 4),
        "gross_pnl":       round(gross_pnl, 2),
        "net_pnl":         round(net_pnl, 2),
        "total_fees":      round(result.total_fees(), 2),
        "total_slippage":  round(result.total_slippage(), 2),
        "avg_holding_h":   round(float(np.mean(holding_h)) if holding_h else 0.0, 2),
        "turnover":        round(turnover, 4),
        "pnl_by_year":     pnl_by_year,
        "pnl_by_fold":     pnl_by_fold,
        "pnl_by_model":    pnl_by_model,
        "cost_sensitivity": {
            f"pf_x{m}": round(_pf_with_mult(m), 4) for m in [1, 2, 3]
        },
    }


# ─── Sauvegarde des outputs ────────────────────────────────────────────────────

def save_backtest_outputs(
    result:  BacktestResult,
    metrics: Dict,
    out_dir: Path,
    portfolio_name: str = "portfolio",
    engine_name:    str = "INSTITUTIONAL",
) -> None:
    """Sauvegarde tous les outputs du backtest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # orders.parquet
    if result.orders:
        pd.DataFrame([{
            "order_id": o.order_id, "timestamp": o.ts, "asset": o.asset,
            "side": o.side, "size_usd": o.size_usd,
            "price_ref": o.price_ref, "signal_prob": o.signal_prob,
        } for o in result.orders]).to_parquet(out_dir / "orders.parquet", index=False)

    # fills.parquet
    if result.fills:
        pd.DataFrame([{
            "fill_id": f.fill_id, "order_id": f.order_id, "timestamp": f.ts,
            "asset": f.asset, "side": f.side, "size_usd": f.size_usd,
            "price": f.price, "fee": f.fee,
            "slippage_cost": f.slippage_cost, "fill_type": f.fill_type,
        } for f in result.fills]).to_parquet(out_dir / "fills.parquet", index=False)

    # positions.parquet
    if result.position_bars:
        pd.DataFrame(result.position_bars).to_parquet(out_dir / "positions.parquet", index=False)

    # equity_curve.parquet
    eq = result.equity_series()
    if not eq.empty:
        eq.to_frame("equity").to_parquet(out_dir / "equity_curve.parquet")

    # trades.parquet
    closed = [t for t in result.trades if not t.is_open]
    if closed:
        pd.DataFrame([{
            "trade_id":   t.trade_id,   "asset":       t.asset,
            "direction":  t.direction,
            "entry_ts":   t.entry_ts,   "entry_price": t.entry_price,
            "exit_ts":    t.exit_ts,    "exit_price":  t.exit_price,
            "size_usd":   t.size_usd,   "pnl_gross":   t.pnl_gross,
            "pnl_net":    t.pnl_net,    "fees":        t.entry_fee + t.exit_fee,
            "exit_reason": t.exit_reason,
            "fold_year":  t.fold_year,  "model_path":  t.model_path,
            "model_type": t.model_type, "prediction":  t.prediction,
            "threshold":  t.threshold,
        } for t in closed]).to_parquet(out_dir / "trades.parquet", index=False)

    # metrics.json
    metrics["portfolio_name"] = portfolio_name
    metrics["engine"]         = engine_name
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))

    # report.md
    _write_report(metrics, out_dir, portfolio_name, engine_name)
    logger.info(f"Backtest outputs saved: {out_dir}")


def _write_report(metrics: Dict, out_dir: Path, portfolio: str, engine: str) -> None:
    pf     = metrics.get("pf", 0)
    sharpe = metrics.get("sharpe", 0)
    sortino = metrics.get("sortino", 0)
    n_t    = metrics.get("n_trades", 0)
    mdd    = abs(metrics.get("max_drawdown", 1.0))
    cs     = metrics.get("cost_sensitivity", {})
    pf_x2  = cs.get("pf_x2", 0)
    pf_x3  = cs.get("pf_x3", 0)

    # Gates institutionnels officiels
    if pf < 1.00 or pf_x2 < 1.00 or n_t < 30 or sharpe < 0:
        verdict = "REJECT"
    elif pf >= 1.30 and pf_x2 >= 1.10 and pf_x3 >= 1.05 and sharpe >= 0.80 and n_t >= 150 and mdd < 0.18:
        verdict = "PROMOTE"
    elif pf >= 1.25 and pf_x2 >= 1.05 and sharpe >= 0.60 and n_t >= 100 and mdd < 0.20:
        verdict = "PAPER"
    elif pf >= 1.05 and n_t >= 50 and mdd < 0.20:
        verdict = "INCUBATE"
    else:
        verdict = "REJECT"

    # PnL par année
    pnl_year_lines = []
    for yr, pnl in sorted(metrics.get("pnl_by_year", {}).items()):
        pnl_year_lines.append(f"| {yr} | {pnl:+.2f} USD |")

    # PnL par fold
    pnl_fold_lines = []
    for fold, pnl in sorted(metrics.get("pnl_by_fold", {}).items()):
        pnl_fold_lines.append(f"| fold_{fold} | {pnl:+.2f} USD |")

    lines = [
        f"# Backtest Report — {portfolio} ({engine})",
        f"",
        f"## Métriques principales",
        f"| Métrique | Valeur |",
        f"|---|---|",
        f"| PF cost×1   | {pf:.4f} |",
        f"| PF cost×2   | {pf_x2:.4f} |",
        f"| PF cost×3   | {pf_x3:.4f} |",
        f"| Sharpe      | {sharpe:.4f} |",
        f"| Sortino     | {sortino:.4f} |",
        f"| CAGR        | {metrics.get('cagr', 0):.2%} |",
        f"| Max DD      | {metrics.get('max_drawdown', 0):.2%} |",
        f"| N trades    | {n_t} |",
        f"| Hit rate    | {metrics.get('hit_rate', 0):.2%} |",
        f"| Expectancy  | {metrics.get('expectancy', 0):.4f} USD |",
        f"| Avg Win     | {metrics.get('avg_win', 0):.4f} USD |",
        f"| Avg Loss    | {metrics.get('avg_loss', 0):.4f} USD |",
        f"| W/L ratio   | {metrics.get('win_loss_ratio', 0):.4f} |",
        f"| Avg holding | {metrics.get('avg_holding_h', 0):.1f}h |",
        f"| Turnover    | {metrics.get('turnover', 0):.2f}× |",
        f"",
        f"## PnL",
        f"- Gross PnL : {metrics.get('gross_pnl', 0):.2f} USD",
        f"- Net PnL   : {metrics.get('net_pnl', 0):.2f} USD",
        f"- Fees paid : {metrics.get('total_fees', 0):.2f} USD",
        f"- Slippage  : {metrics.get('total_slippage', 0):.2f} USD",
        f"",
    ]

    if pnl_year_lines:
        lines += [
            f"## PnL par année",
            f"| Année | PnL net |",
            f"|---|---|",
        ] + pnl_year_lines + [""]

    if pnl_fold_lines:
        lines += [
            f"## PnL par fold modèle",
            f"| Fold | PnL net |",
            f"|---|---|",
        ] + pnl_fold_lines + [""]

    lines += [
        f"## Verdict",
        f"",
        f"**{verdict}**",
        f"",
        f"| Gate    | Critères |",
        f"|---|---|",
        f"| REJECT  | PF×1<1.00 OU PF×2<1.00 OU N<30 OU Sharpe<0 |",
        f"| INCUBATE| PF×1∈[1.05,1.25) ET N≥50 ET MaxDD<20% |",
        f"| PAPER   | PF×1≥1.25 ET PF×2≥1.05 ET Sharpe≥0.60 ET N≥100 ET MaxDD<20% |",
        f"| PROMOTE | PF×1≥1.30 ET PF×2≥1.10 ET PF×3≥1.05 ET Sharpe≥0.80 ET N≥150 ET MaxDD<18% |",
    ]
    (out_dir / "report.md").write_text("\n".join(lines))
