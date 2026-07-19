"""
src/institutional/risk/governor.py
─────────────────────────────────────────────────────────────────────────────
CASH_HEDGE_GOVERNOR — gouverneur de survie (≠ moteur alpha).

C'est lui qui remplace l'absence de SHORT directionnel : en stress, il réduit
les tailles, interdit de nouveaux longs, passe cash ou active un hedge lié.

États : risk_on / risk_reduced / cash / hedged / kill
Sortie principale : un multiplicateur de taille global ∈ [0, 1] (0 = halt).

Inputs : régime BTC, régime de vol, drawdown courant, drift modèle, spike de
corrélation, risque de liquidité. Réutilise l'esprit de risk/kill_switch.py et
ai/regime/composite.py sans les coupler en dur.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# multiplicateur de taille par état
STATE_MULT: Dict[str, float] = {
    "risk_on": 1.0,
    "risk_reduced": 0.5,
    "hedged": 0.5,
    "cash": 0.0,
    "kill": 0.0,
}


@dataclass
class GovernorConfig:
    reduce_drawdown: float = 0.02      # -2% depuis peak → risk_reduced
    cash_drawdown: float = 0.025       # -2.5% → cash (plus de nouveaux longs)
    kill_drawdown: float = 0.03        # -3% depuis peak → kill (gate DD≤3%)
    bear_regimes: tuple = ("NO_LONG", "HARD_BEAR", "PANIC", "DELEVERAGING")
    drift_kill: float = 1.0            # drift normalisé ≥ 1 → kill
    corr_spike: float = 0.9            # corrélation moyenne ≥ 0.9 → risk_reduced


# Politique durcie (Phase 19) : coupe AVANT 3% car latence/gaps/slippage poussent
# souvent un kill à 3% vers 3.3-3.8% réel. Survie > esthétique d'une période.
CONSERVATIVE_V1 = GovernorConfig(
    reduce_drawdown=0.01,    # -1% → réduction
    cash_drawdown=0.020,     # -2% → cash (interdit nouveaux longs)
    kill_drawdown=0.025,     # -2.5% → kill
)


@dataclass
class GovernorDecision:
    state: str
    size_mult: float
    reason: str


class RiskGovernor:
    """Décide de l'état de risque global et du multiplicateur de taille."""

    def __init__(
        self,
        config: Optional[GovernorConfig] = None,
        btc_regime: Optional[pd.Series] = None,  # série régime BTC indexée par ts
    ):
        self.config = config or GovernorConfig()
        self.btc_regime = btc_regime
        self._peak_equity = 0.0

    def _regime_at(self, ts: pd.Timestamp) -> str:
        if self.btc_regime is None or len(self.btc_regime) == 0:
            return "UNKNOWN"
        idx = self.btc_regime.index.searchsorted(ts, side="right") - 1
        return str(self.btc_regime.iloc[idx]) if idx >= 0 else "UNKNOWN"

    def decide(
        self,
        timestamp: pd.Timestamp,
        equity: float,
        *,
        drift: float = 0.0,
        avg_correlation: float = 0.0,
        liquidity_ok: bool = True,
    ) -> GovernorDecision:
        cfg = self.config
        self._peak_equity = max(self._peak_equity, equity)
        dd = (equity - self._peak_equity) / max(self._peak_equity, 1e-9)

        # 1. kill conditions (survie absolue)
        if dd <= -cfg.kill_drawdown:
            return GovernorDecision("kill", 0.0, f"drawdown {dd:.2%} ≤ -{cfg.kill_drawdown:.0%}")
        if drift >= cfg.drift_kill:
            return GovernorDecision("kill", 0.0, f"model_drift {drift:.2f}")

        # 2. cash (régime bear hostile → pas de nouveaux longs)
        regime = self._regime_at(timestamp)
        if regime in cfg.bear_regimes:
            return GovernorDecision("cash", 0.0, f"bear_regime {regime}")

        # 3. cash (interdit nouveaux longs ; exits restent autorisés)
        if dd <= -cfg.cash_drawdown:
            return GovernorDecision("cash", 0.0, f"drawdown {dd:.2%} ≤ -{cfg.cash_drawdown:.1%}")

        # 4. risk_reduced
        if dd <= -cfg.reduce_drawdown:
            return GovernorDecision("risk_reduced", STATE_MULT["risk_reduced"], f"drawdown {dd:.2%}")
        if avg_correlation >= cfg.corr_spike:
            return GovernorDecision("risk_reduced", STATE_MULT["risk_reduced"],
                                    f"correlation_spike {avg_correlation:.2f}")
        if not liquidity_ok:
            return GovernorDecision("hedged", STATE_MULT["hedged"], "liquidity_stress")

        return GovernorDecision("risk_on", 1.0, "ok")

    def as_hook(self):
        """Adapter pour PortfolioBacktester.governor_hook : f(ts, ctx) -> mult."""
        def _hook(ts: pd.Timestamp, ctx: Dict) -> float:
            d = self.decide(ts, float(ctx.get("equity", 0.0)))
            return d.size_mult
        return _hook
