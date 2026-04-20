from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from domain.signal.signal import DecisionStatus, Signal, SignalDirection, TradeMode


class SignalBuilder:
    def __init__(self, threshold_bps: float = 5.0):
        self.threshold = threshold_bps / 10_000

    def build(
        self,
        state: pd.Series,
        gating: Dict,
        regime_out: Dict,
        edge_out: Dict,
        comp_out: Dict,
        run_id: str,
        model_version: str,
    ) -> Signal:
        q50 = float(edge_out.get("q50", 0.0))
        direction = SignalDirection.FLAT
        if q50 > self.threshold:
            direction = SignalDirection.LONG
        elif q50 < -self.threshold:
            direction = SignalDirection.SHORT
        tradeable = gating.get("tradeable", True)
        mode = gating.get("mode", TradeMode.TAKER)
        coarse_direction = gating.get("coarse_direction", SignalDirection.FLAT)
        reasons: List[str] = gating.get("veto_reasons", [])
        confidence_raw = float(edge_out.get("p_hit", 0.5))
        confidence_calibrated = float(edge_out.get("p_hit", 0.5))
        signal = Signal(
            event_time=state.get("event_time"),
            symbol=str(state.get("symbol")),
            tradeable=tradeable,
            mode=mode if tradeable else TradeMode.OFF,
            direction=direction if tradeable else SignalDirection.FLAT,
            decision_status=DecisionStatus.CONFIRM,
            coarse_direction=coarse_direction,
            regime_probs=regime_out.get("regime_probs", {}),
            regime_entropy=float(regime_out.get("regime_entropy", 0.0)),
            quantiles={"Q05": float(edge_out.get("q05", 0.0)), "Q50": float(q50), "Q95": float(edge_out.get("q95", 0.0))},
            p_hit=float(edge_out.get("p_hit", 0.5)),
            expected_shortfall=float(edge_out.get("expected_shortfall", 0.0)),
            rv_fwd={"mean": float(edge_out.get("rv_mean", q50)), "q50": float(q50), "q95": float(edge_out.get("q95", 0.0))},
            confidence_raw=confidence_raw,
            confidence_calibrated=confidence_calibrated,
            novelty_score=float(comp_out.get("novelty_score", 0.0)),
            disagreement_score=float(comp_out.get("disagreement_score", 0.0)),
            quality_flags=int(state.get("quality_flags", 0)),
            reasons=reasons,
            model_version=model_version,
            run_id=run_id,
            feature_set=str(state.get("feature_set", "v1")),
            model_stack="v1",
        )
        return signal
