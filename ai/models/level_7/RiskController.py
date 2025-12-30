# risk_controller.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
import numpy as np


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if not np.isfinite(v):
            return float(default)
        return v
    except Exception:
        return float(default)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass(frozen=True)
class RiskConfig:
    # Account / budgets
    equity: float = 10_000.0
    risk_per_trade: float = 0.002          # 0.2% equity
    max_gross_exposure: float = 1.0        # 1x equity notionnel
    max_position_notional: float = 1.0     # fraction equity

    # Stops / volatility
    use_atr: bool = True
    atr_key: str = "atr_14"                # in price units
    rv_key: str = "rv_60"                  # in log-return units (small)
    stop_atr_mult: float = 2.5
    stop_rv_mult: float = 3.0              # fallback
    min_stop_pct: float = 0.001            # 0.1%
    max_stop_pct: float = 0.03             # 3%

    # Take profit (optional)
    rr: float = 1.5                        # take_profit = rr * stop

    # Trade filters
    min_abs_edge: float = 0.05             # minimal |edge_final|
    min_scale: float = 0.15                # meta scale minimal
    cooldown_bars: int = 3

    # Daily protections
    daily_loss_limit_pct: float = 0.02     # stop trading after -2% day
    max_consecutive_losses: int = 3


@dataclass
class RiskState:
    last_trade_bar: int = -10_000
    day_start_equity: float = 10_000.0
    consecutive_losses: int = 0
    day_pnl: float = 0.0


class RiskController:
    """
    Inputs attendus par decide():
      - price: float (Close)
      - edge_final: float (signé)
      - scale: float (0..1)
      - tradeable: bool (optionnel, gating L0)
      - bar_index: int
      - features: dict (doit contenir atr_14 et/ou rv_60 selon config)

    Output:
      dict {
        action: "BUY"|"SELL"|"HOLD",
        qty: float,
        notional: float,
        stop_price: float|None,
        take_profit: float|None,
        reason: str,
      }
    """

    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.state = RiskState(day_start_equity=cfg.equity)

    # -------------------------
    # Public: update PnL / day
    # -------------------------
    def on_fill_pnl(self, realized_pnl: float, new_equity: Optional[float] = None) -> None:
        pnl = float(realized_pnl)
        self.state.day_pnl += pnl
        if pnl < 0:
            self.state.consecutive_losses += 1
        elif pnl > 0:
            self.state.consecutive_losses = 0
        if new_equity is not None and np.isfinite(new_equity):
            # keep equity consistent with external system
            object.__setattr__(self.cfg, "equity", float(new_equity))  # if you want mutable, remove frozen dataclass

    def reset_day(self, equity: Optional[float] = None) -> None:
        if equity is None:
            equity = self.cfg.equity
        self.state.day_start_equity = float(equity)
        self.state.day_pnl = 0.0
        self.state.consecutive_losses = 0

    # -------------------------
    # Internal helpers
    # -------------------------
    def _daily_stop(self) -> bool:
        eq0 = self.state.day_start_equity
        if eq0 <= 0:
            return True
        return (self.state.day_pnl <= -self.cfg.daily_loss_limit_pct * eq0) or (
            self.state.consecutive_losses >= self.cfg.max_consecutive_losses
        )

    def _cooldown_ok(self, bar_index: int) -> bool:
        return (bar_index - self.state.last_trade_bar) >= self.cfg.cooldown_bars

    def _stop_distance_pct(self, price: float, features: Dict[str, Any]) -> float:
        price = max(price, 1e-9)

        if self.cfg.use_atr:
            atr = _f(features.get(self.cfg.atr_key, 0.0), 0.0)
            if atr > 0:
                stop_pct = (self.cfg.stop_atr_mult * atr) / price
                return _clip(stop_pct, self.cfg.min_stop_pct, self.cfg.max_stop_pct)

        # fallback rv -> convert log-vol to rough pct move
        rv = _f(features.get(self.cfg.rv_key, 0.0), 0.0)
        stop_pct = self.cfg.stop_rv_mult * abs(rv)
        return _clip(stop_pct, self.cfg.min_stop_pct, self.cfg.max_stop_pct)

    # -------------------------
    # Main decision
    # -------------------------
    def decide(
        self,
        *,
        price: float,
        edge_final: float,
        scale: float,
        bar_index: int,
        features: Dict[str, Any],
        tradeable: Optional[bool] = None,
        current_gross_exposure_frac: float = 0.0,   # notionnel / equity
    ) -> Dict[str, Any]:

        price = _f(price, 0.0)
        edge_final = _f(edge_final, 0.0)
        scale = _f(scale, 0.0)

        if price <= 0:
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "bad_price"}

        if self._daily_stop():
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "daily_stop"}

        if tradeable is False:
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "not_tradeable"}

        if scale < self.cfg.min_scale:
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "low_scale"}

        if abs(edge_final) < self.cfg.min_abs_edge:
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "low_edge"}

        if not self._cooldown_ok(bar_index):
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "cooldown"}

        # Exposure cap
        if current_gross_exposure_frac >= self.cfg.max_gross_exposure:
            return {"action": "HOLD", "qty": 0.0, "notional": 0.0, "stop_price": None, "take_profit": None, "reason": "exposure_cap"}

        # Direction
        action = "BUY" if edge_final > 0 else "SELL"

        # Stop distance
        stop_pct = self._stop_distance_pct(price, features)
        stop_dist = stop_pct * price

        # Risk budget in currency
        equity = float(self.cfg.equity)
        risk_budget = equity * float(self.cfg.risk_per_trade)

        # Qty sizing: risk_budget = qty * stop_dist
        qty = risk_budget / max(stop_dist, 1e-9)

        # Notional cap
        max_notional = equity * float(self.cfg.max_position_notional)
        notional = qty * price
        if notional > max_notional:
            qty = max_notional / price
            notional = qty * price

        # Stop / TP prices
        if action == "BUY":
            stop_price = price - stop_dist
            take_profit = price + self.cfg.rr * stop_dist
        else:
            stop_price = price + stop_dist
            take_profit = price - self.cfg.rr * stop_dist

        # Mark last trade time
        self.state.last_trade_bar = int(bar_index)

        return {
            "action": action,
            "qty": float(qty),
            "notional": float(notional),
            "stop_price": float(stop_price),
            "take_profit": float(take_profit),
            "reason": "ok",
            "stop_pct": float(stop_pct),
            "risk_budget": float(risk_budget),
        }
