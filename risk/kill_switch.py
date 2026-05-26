"""
risk/kill_switch.py — Multi-Level Kill Switch

3 niveaux indépendants d'arrêt automatique:

  1. Intraday (hot):  perte > X% en 1h → halt N barres
  2. Weekly:          drawdown > Y% sur 7 jours → halt 7 jours
  3. VaR breach:      perte réalisée < -2σ CVaR → halt

Chaque niveau peut être actif indépendamment.
Le niveau le plus restrictif prend le dessus.

Usage:
  ks = KillSwitch()
  ks.update(bar_index=t, realized_pnl=-0.015, portfolio_var=var_report)
  decision = ks.check()
  if not decision.allow_trading:
      # stopper toutes les nouvelles positions
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from risk.portfolio_var import VaRReport


@dataclass
class KillDecision:
    allow_trading:   bool
    active_switches: list[str]           # switches actifs
    remaining_bars:  int                 # 0 si allow_trading
    reason:          str

    def summary(self) -> dict:
        return {
            "allow_trading":   self.allow_trading,
            "active_switches": self.active_switches,
            "remaining_bars":  self.remaining_bars,
            "reason":          self.reason,
        }


@dataclass
class _SwitchState:
    active:        bool  = False
    halt_until:    int   = 0     # bar index jusqu'auquel halter
    trigger_count: int   = 0     # nombre de déclenchements total


class KillSwitch:
    """
    Kill switch multi-niveaux avec logging des déclenchements.

    Niveaux:
      intraday  — perte horaire > intraday_loss_pct → halt intraday_halt_bars barres
      weekly    — drawdown 7j > weekly_dd_pct → halt weekly_halt_bars barres
      var_breach— perte > CVaR_99 → halt var_halt_bars barres
    """

    def __init__(
        self,
        intraday_loss_pct:  float = 0.01,   # 1% en 1h → halt
        intraday_halt_bars: int   = 12,     # halt 12h
        weekly_dd_pct:      float = 0.05,   # 5% sur 7j → halt
        weekly_halt_bars:   int   = 168,    # halt 1 semaine (168h)
        var_cvar_mult:      float = 2.0,    # perte > 2×CVaR_99 → halt
        var_halt_bars:      int   = 48,     # halt 48h
    ):
        self._intraday_loss = intraday_loss_pct
        self._intraday_halt = intraday_halt_bars
        self._weekly_dd     = weekly_dd_pct
        self._weekly_halt   = weekly_halt_bars
        self._var_mult      = var_cvar_mult
        self._var_halt      = var_halt_bars

        self._states = {
            "intraday":  _SwitchState(),
            "weekly":    _SwitchState(),
            "var_breach": _SwitchState(),
        }

        self._bar_index:     int   = 0
        self._intraday_pnl:  float = 0.0
        self._intraday_start_bar: int = 0
        self._weekly_peak:   float = 1.0   # equity relative
        self._equity_rel:    float = 1.0
        self._events: list[dict] = []

    # ------------------------------------------------------------------
    # Update & check
    # ------------------------------------------------------------------

    def update(
        self,
        bar_index:     int,
        realized_pnl:  float,   # PnL de la barre (fraction du capital)
        portfolio_var: Optional[VaRReport] = None,
    ) -> KillDecision:
        self._bar_index   = bar_index
        self._equity_rel *= (1.0 + realized_pnl)

        # Accumulation intraday (reset chaque 24 barres)
        if bar_index - self._intraday_start_bar >= 24:
            self._intraday_pnl        = 0.0
            self._intraday_start_bar  = bar_index
        self._intraday_pnl += realized_pnl

        # Drawdown hebdo
        if self._equity_rel > self._weekly_peak:
            self._weekly_peak = self._equity_rel
        weekly_dd = (self._equity_rel - self._weekly_peak) / max(self._weekly_peak, 1e-9)

        # Vérification des seuils
        self._check_intraday(bar_index)
        self._check_weekly(bar_index, weekly_dd)
        if portfolio_var is not None:
            self._check_var_breach(bar_index, realized_pnl, portfolio_var)

        return self.check()

    def check(self) -> KillDecision:
        """État actuel du kill switch."""
        active = []
        max_halt = 0

        for name, state in self._states.items():
            if state.active:
                remaining = max(0, state.halt_until - self._bar_index)
                if remaining > 0:
                    active.append(name)
                    max_halt = max(max_halt, remaining)
                else:
                    state.active = False   # Expiration automatique

        allow = len(active) == 0
        reason = "all_clear" if allow else f"halted_by_{','.join(active)}"

        return KillDecision(
            allow_trading  = allow,
            active_switches= active,
            remaining_bars = max_halt,
            reason         = reason,
        )

    def is_active(self) -> bool:
        return not self.check().allow_trading

    def remaining_halt_bars(self) -> int:
        return self.check().remaining_bars

    def reset(self, switch_name: Optional[str] = None) -> None:
        if switch_name:
            self._states[switch_name] = _SwitchState()
        else:
            for name in self._states:
                self._states[name] = _SwitchState()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        data = {
            "bar_index":      self._bar_index,
            "equity_rel":     self._equity_rel,
            "weekly_peak":    self._weekly_peak,
            "intraday_pnl":   self._intraday_pnl,
            "intraday_start": self._intraday_start_bar,
            "states": {
                name: {
                    "active":        s.active,
                    "halt_until":    s.halt_until,
                    "trigger_count": s.trigger_count,
                }
                for name, s in self._states.items()
            },
            "events": self._events[-50:],  # garder les 50 derniers
        }
        Path(path).write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        data = json.loads(Path(path).read_text())
        self._bar_index         = data["bar_index"]
        self._equity_rel        = data["equity_rel"]
        self._weekly_peak       = data["weekly_peak"]
        self._intraday_pnl      = data["intraday_pnl"]
        self._intraday_start_bar= data["intraday_start"]
        for name, s in data["states"].items():
            self._states[name] = _SwitchState(**s)
        self._events = data.get("events", [])

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _check_intraday(self, bar_index: int) -> None:
        if self._intraday_pnl < -self._intraday_loss:
            state = self._states["intraday"]
            if not state.active:
                state.active        = True
                state.halt_until    = bar_index + self._intraday_halt
                state.trigger_count += 1
                self._log_event("intraday", bar_index, self._intraday_pnl)

    def _check_weekly(self, bar_index: int, weekly_dd: float) -> None:
        if weekly_dd < -self._weekly_dd:
            state = self._states["weekly"]
            if not state.active:
                state.active        = True
                state.halt_until    = bar_index + self._weekly_halt
                state.trigger_count += 1
                self._log_event("weekly", bar_index, weekly_dd)

    def _check_var_breach(
        self,
        bar_index: int,
        realized_pnl: float,
        var_report: VaRReport,
    ) -> None:
        threshold = -var_report.cvar_99 * self._var_mult
        if realized_pnl < threshold:
            state = self._states["var_breach"]
            if not state.active:
                state.active        = True
                state.halt_until    = bar_index + self._var_halt
                state.trigger_count += 1
                self._log_event("var_breach", bar_index, realized_pnl)

    def _log_event(self, switch: str, bar_index: int, value: float) -> None:
        self._events.append({
            "switch":    switch,
            "bar":       bar_index,
            "value":     round(value, 6),
            "triggers":  self._states[switch].trigger_count,
        })

    def event_log(self) -> list[dict]:
        return self._events.copy()
