"""
ai/regime/vol_state_machine.py — Volatility State Machine (3 états)

États:
  LOW_VOL   — rv_ratio_24_72 < 0.7  (marché calme)
  NORMAL    — rv_ratio_24_72 ∈ [0.7, 1.6] (régime standard)
  STRESS    — rv_ratio_24_72 > 1.6 OU atr_pct_14 > seuil_stress

Hystérésis: 3 barres minimum avant transition (évite le ping-pong).

Usage:
  fsm = VolatilityFSM()
  state = fsm.update(bar_dict)
  print(fsm.current_state, fsm.time_in_state())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VolState(str, Enum):
    LOW_VOL = "LOW_VOL"
    NORMAL  = "NORMAL"
    STRESS  = "STRESS"


@dataclass
class _Transition:
    target:    VolState
    count:     int = 0
    required:  int = 3    # barres consécutives avant de valider la transition


class VolatilityFSM:
    """
    Finite-state machine sur la volatilité réalisée.

    Paramètres:
      low_threshold    — rv_ratio_24_72 ≤ low → candidat LOW_VOL
      stress_threshold — rv_ratio_24_72 ≥ stress → candidat STRESS
      atr_stress       — atr_pct_14 ≥ atr_stress → renforce STRESS
      hysteresis_bars  — barres consécutives nécessaires pour changer d'état
    """

    def __init__(
        self,
        low_threshold:    float = 0.70,
        stress_threshold: float = 1.60,
        atr_stress:       float = 0.025,
        hysteresis_bars:  int   = 3,
    ):
        self._low    = low_threshold
        self._stress = stress_threshold
        self._atr_s  = atr_stress
        self._hyster = hysteresis_bars

        self._state:       VolState        = VolState.NORMAL
        self._bars_in:     int             = 0
        self._pending:     _Transition | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> VolState:
        return self._state

    def time_in_state(self) -> int:
        return self._bars_in

    def update(self, bar: dict) -> VolState:
        rv_ratio = float(bar.get("rv_ratio_24_72", 1.0))
        atr_pct  = float(bar.get("atr_pct_14",    0.015))

        target = self._classify(rv_ratio, atr_pct)
        self._bars_in += 1

        if target == self._state:
            self._pending = None
            return self._state

        # Gestion des transitions avec hystérésis
        if self._pending is not None and self._pending.target == target:
            self._pending.count += 1
            if self._pending.count >= self._hyster:
                self._state   = target
                self._bars_in = 0
                self._pending = None
        else:
            self._pending = _Transition(target=target, count=1, required=self._hyster)

        return self._state

    def state_multipliers(self) -> dict[str, float]:
        """Multiplicateurs de taille recommandés par état."""
        return {
            VolState.LOW_VOL: {"long": 1.2, "short": 0.8},
            VolState.NORMAL:  {"long": 1.0, "short": 1.0},
            VolState.STRESS:  {"long": 0.5, "short": 1.3},
        }[self._state]

    def reset(self) -> None:
        self._state   = VolState.NORMAL
        self._bars_in = 0
        self._pending = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _classify(self, rv_ratio: float, atr_pct: float) -> VolState:
        if rv_ratio >= self._stress or atr_pct >= self._atr_s:
            return VolState.STRESS
        if rv_ratio <= self._low:
            return VolState.LOW_VOL
        return VolState.NORMAL
