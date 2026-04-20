from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict


class SignalDirection(str, Enum):
    SHORT = "SHORT"
    FLAT = "FLAT"
    LONG = "LONG"


class TradeMode(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    OFF = "OFF"


class DecisionStatus(str, Enum):
    INVALIDATE = "INVALIDATE"
    DELAY = "DELAY"
    CONFIRM = "CONFIRM"


@dataclass
class Signal:
    event_time: pd.Timestamp
    symbol: str
    tradeable: bool
    mode: TradeMode
    direction: SignalDirection
    decision_status: DecisionStatus
    coarse_direction: SignalDirection
    regime_probs: Dict[str, float]
    regime_entropy: float
    quantiles: Dict[str, float]
    p_hit: float
    expected_shortfall: float
    rv_fwd: Dict[str, float]
    confidence_raw: float
    confidence_calibrated: float
    novelty_score: float
    disagreement_score: float
    quality_flags: int
    reasons: List[str] = field(default_factory=list)
    model_version: str = "v1"
    run_id: str = ""
    feature_set: str = "v1"
    model_stack: str = "v1"

    def to_dict(self, flatten: bool = True) -> Dict[str, object]:
        data = {
            "event_time": pd.to_datetime(self.event_time),
            "symbol": self.symbol,
            "feature_set": self.feature_set,
            "model_stack": self.model_stack,
            "tradeable": bool(self.tradeable),
            "mode": self.mode.value,
            "direction": self.direction.value,
            "decision_status": self.decision_status.value,
            "coarse_direction": self.coarse_direction.value,
            "regime_entropy": float(self.regime_entropy),
            "confidence_raw": float(self.confidence_raw),
            "confidence_calibrated": float(self.confidence_calibrated),
            "p_hit": float(self.p_hit),
            "expected_shortfall": float(self.expected_shortfall),
            "novelty_score": float(self.novelty_score),
            "disagreement_score": float(self.disagreement_score),
            "quality_flags": int(self.quality_flags),
            "reasons": self.reasons,
            "model_version": self.model_version,
            "run_id": self.run_id,
        }
        if flatten:
            data.update({f"regime_prob_{k}": float(v) for k, v in self.regime_probs.items()})
            data.update({k.lower(): float(v) for k, v in self.quantiles.items()})
            data["rv_fwd_mean"] = float(self.rv_fwd.get("mean", 0.0))
            data["rv_fwd_q50"] = float(self.rv_fwd.get("q50", self.quantiles.get("q50", 0.0)))
            data["rv_fwd_q95"] = float(self.rv_fwd.get("q95", self.quantiles.get("q95", 0.0)))
        else:
            data["regime_probs"] = self.regime_probs
            data["quantiles"] = self.quantiles
            data["rv_fwd"] = self.rv_fwd
        return data


class SignalModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    signal: Signal
