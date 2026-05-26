"""
ai/regime/composite.py — Composite Regime Classifier

Agrège 3 sources:
  1. GaussianHMMEngine  → état latent 0/1/2 + probabilités
  2. VolatilityFSM      → LOW_VOL / NORMAL / STRESS
  3. LiquidityStress    → LIQUID / STRESSED / ILLIQUID

Produit un RegimeState structurel:
  EXPANSION      — bull trend + liquid + normal-high vol
  COMPRESSION    — range + low vol + liquid
  DELEVERAGING   — bear trend + normal vol + declining OI
  PANIC          — crash + illiquid + vol extrême + funding extrême
  SQUEEZE        — forced flows (liq spike) + vol stress
  RECOVERY       — post-bear + vol normalisant

Multiplicateurs de sizing par régime:
  EXPANSION    → long ×1.0, short ×0.5
  COMPRESSION  → long ×0.7, short ×0.7
  DELEVERAGING → long ×0.3, short ×1.0
  PANIC        → long ×0.0, short ×0.3
  SQUEEZE      → long ×0.5, short ×0.2   (squeezé des deux côtés)
  RECOVERY     → long ×0.6, short ×0.4

Usage:
  cr = CompositeRegime(hmm_engine, vol_fsm, liquidity_engine)
  cr.fit(df, train_mask)
  state = cr.classify(bar, hmm_probs, vol_state, liquidity_score)
  mults = cr.sizing_multipliers(state)
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from ai.regime.hmm_engine import GaussianHMMEngine
from ai.regime.vol_state_machine import VolatilityFSM, VolState
from ai.regime.liquidity_stress import LiquidityStressEngine


class RegimeState(str, Enum):
    EXPANSION    = "EXPANSION"
    COMPRESSION  = "COMPRESSION"
    DELEVERAGING = "DELEVERAGING"
    PANIC        = "PANIC"
    SQUEEZE      = "SQUEEZE"
    RECOVERY     = "RECOVERY"
    UNKNOWN      = "UNKNOWN"


# Sizing multipliers: {state → {side → multiplier}}
_MULTIPLIERS: dict[RegimeState, dict[str, float]] = {
    RegimeState.EXPANSION:    {"long": 1.00, "short": 0.50},
    RegimeState.COMPRESSION:  {"long": 0.70, "short": 0.70},
    RegimeState.DELEVERAGING: {"long": 0.30, "short": 1.00},
    RegimeState.PANIC:        {"long": 0.00, "short": 0.30},
    RegimeState.SQUEEZE:      {"long": 0.50, "short": 0.20},
    RegimeState.RECOVERY:     {"long": 0.60, "short": 0.40},
    RegimeState.UNKNOWN:      {"long": 0.50, "short": 0.50},
}

# Allowed sides per regime
_ALLOWED_SIDES: dict[RegimeState, list[str]] = {
    RegimeState.EXPANSION:    ["long", "short"],
    RegimeState.COMPRESSION:  ["long", "short"],
    RegimeState.DELEVERAGING: ["short"],
    RegimeState.PANIC:        [],
    RegimeState.SQUEEZE:      ["long"],
    RegimeState.RECOVERY:     ["long"],
    RegimeState.UNKNOWN:      ["long"],
}


class CompositeRegime:
    """
    Moteur de régime composite combinant HMM + VolFSM + LiquidityStress.

    Peut être utilisé en mode autonome (sans HMM) si les features sont insuffisantes.
    """

    HMM_BEAR = 0
    HMM_NEUTRAL = 1
    HMM_BULL = 2

    def __init__(
        self,
        hmm_engine:        Optional[GaussianHMMEngine] = None,
        vol_fsm:           Optional[VolatilityFSM]     = None,
        liquidity_engine:  Optional[LiquidityStressEngine] = None,
        hmm_weight:        float = 0.50,
        vol_weight:        float = 0.25,
        liq_weight:        float = 0.25,
    ):
        self.hmm = hmm_engine or GaussianHMMEngine()
        self.vol = vol_fsm or VolatilityFSM()
        self.liq = liquidity_engine or LiquidityStressEngine()
        self._w  = {"hmm": hmm_weight, "vol": vol_weight, "liq": liq_weight}

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame, train_mask: Optional[pd.Series] = None) -> "CompositeRegime":
        self.hmm.fit(df, train_mask)
        return self

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def classify(
        self,
        bar: dict,
        hmm_probs: Optional[np.ndarray] = None,
    ) -> RegimeState:
        """
        Classifie le régime structurel à partir d'une barre de données.

        Args:
            bar       : dict de features (ou pd.Series)
            hmm_probs : (n_states,) probabilités HMM. Si None, calculé depuis `bar`.
        """
        if isinstance(bar, pd.Series):
            bar = bar.to_dict()

        vol_state  = self.vol.update(bar)
        liq_score  = self.liq.score(bar)
        liq_regime = self.liq.regime(bar)

        # HMM probabilities
        if hmm_probs is None:
            if self.hmm._fitted:
                row = pd.Series(bar)
                state_id, conf = self.hmm.current_state(row)
                # Soft assignment: dominant state gets conf, others split the rest
                n = self.hmm.n_states
                hmm_probs = np.full(n, (1.0 - conf) / max(n - 1, 1))
                hmm_probs[state_id] = conf
            else:
                # Fallback: use simple rule-based bear/neutral/bull
                hmm_probs = self._rule_based_probs(bar)

        p_bear   = float(hmm_probs[self.HMM_BEAR])
        p_neutral= float(hmm_probs[self.HMM_NEUTRAL]) if len(hmm_probs) > 1 else 0.0
        p_bull   = float(hmm_probs[self.HMM_BULL])   if len(hmm_probs) > 2 else 0.0

        return self._decision(p_bear, p_neutral, p_bull, vol_state, liq_score, liq_regime, bar)

    def regime_probs(self, bar: dict) -> dict[str, float]:
        """Probabilités softs sur tous les régimes (pour monitoring)."""
        state = self.classify(bar)
        base = {r.value: 0.05 for r in RegimeState}
        base[state.value] = 0.80
        total = sum(base.values())
        return {k: round(v / total, 4) for k, v in base.items()}

    def sizing_multipliers(self, state: RegimeState) -> dict[str, float]:
        return _MULTIPLIERS.get(state, _MULTIPLIERS[RegimeState.UNKNOWN])

    def allowed_sides(self, state: RegimeState) -> list[str]:
        return _ALLOWED_SIDES.get(state, ["long"])

    # ------------------------------------------------------------------
    # Decision rules
    # ------------------------------------------------------------------

    def _decision(
        self,
        p_bear:      float,
        p_neutral:   float,
        p_bull:      float,
        vol_state:   VolState,
        liq_score:   float,
        liq_regime:  str,
        bar:         dict,
    ) -> RegimeState:

        mom_72 = float(bar.get("mom_logret_72", 0.0))
        oi_z   = float(bar.get("oi_acceleration_z", 0.0))

        # PANIC: crash + illiquid + extreme vol (strict — rare event)
        if liq_regime == "ILLIQUID" and p_bear > 0.4 and vol_state == VolState.STRESS:
            return RegimeState.PANIC

        # SQUEEZE: liquidation spikes + vol stress
        liq_long  = float(bar.get("liq_long_spike_12",  0.0))
        liq_short = float(bar.get("liq_short_spike_12", 0.0))
        if (liq_long > 2.0 or liq_short > 2.0) and vol_state == VolState.STRESS:
            return RegimeState.SQUEEZE

        # COMPRESSION: range + low vol (check before trend regimes)
        if vol_state == VolState.LOW_VOL and abs(mom_72) < 0.05:
            return RegimeState.COMPRESSION

        # DELEVERAGING: bear-dominant + declining OI or clear negative momentum
        if p_bear > 0.35 and (oi_z < -0.3 or mom_72 < -0.02):
            return RegimeState.DELEVERAGING

        # RECOVERY: bear residual but momentum turning positive (post-crash rebound)
        if 0.20 < p_bear <= 0.50 and mom_72 > 0.005 and vol_state != VolState.STRESS:
            return RegimeState.RECOVERY

        # EXPANSION: bull-dominant or sustained positive momentum
        if p_bull > 0.30 and mom_72 > 0.005 and liq_regime != "ILLIQUID":
            return RegimeState.EXPANSION
        if mom_72 > 0.03 and liq_regime != "ILLIQUID":
            return RegimeState.EXPANSION

        # Fallback: COMPRESSION (range/low-information) rather than UNKNOWN
        if abs(mom_72) < 0.03 or vol_state == VolState.LOW_VOL:
            return RegimeState.COMPRESSION

        # Last resort: directional default based on dominant probability
        dominant = max((p_bear, "DELEVERAGING"), (p_bull, "EXPANSION"), (p_neutral, "COMPRESSION"))
        return RegimeState[dominant[1]]

    def _rule_based_probs(self, bar: dict) -> np.ndarray:
        """Fallback regime detection when HMM is not fitted."""
        mom_72   = float(bar.get("mom_logret_72", 0.0))
        rv_24    = float(bar.get("rv_24", 0.02))
        rv_72    = float(bar.get("rv_72", 0.02))
        rv_ratio = rv_24 / max(rv_72, 1e-6)

        # EMA distance features if available (stronger trend signal)
        dist_ema_50  = float(bar.get("dist_ema_50",  mom_72 * 3))
        dist_ema_200 = float(bar.get("dist_ema_200", mom_72 * 5))

        # Scale ×15 so 3% momentum → 0.45 p_bull (vs ×5 before)
        mom_bull = max(0.0, mom_72 * 15)
        mom_bear = max(0.0, -mom_72 * 15)

        # EMA bonus: above EMA50 and EMA200 → bull signal
        ema_bull = max(0.0, min(0.4, dist_ema_50 * 4 + dist_ema_200 * 2))
        ema_bear = max(0.0, min(0.4, -dist_ema_50 * 4 + -dist_ema_200 * 2))

        # Elevated short-term vol → bearish bias
        vol_bear = max(0.0, min(0.2, (rv_ratio - 1.5) * 0.2))

        p_bull    = min(1.0, mom_bull + ema_bull)
        p_bear    = min(1.0, mom_bear + ema_bear + vol_bear)
        p_neutral = max(0.0, 1.0 - p_bull - p_bear)
        total     = p_bear + p_neutral + p_bull
        return np.array([p_bear, p_neutral, p_bull]) / max(total, 1e-6)
