from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class RiskBudgets:
    max_leverage: float = 3.0
    max_gross_exposure: float = 1.0
    max_dd: float = 0.2
    daily_loss_limit: float = 0.05
    cluster_exposure_caps: Dict[str, float] = field(default_factory=dict)
    asset_weight_caps: Dict[str, float] = field(default_factory=dict)
    meta_leverage_caps_by_regime: Dict[str, float] = field(default_factory=dict)
    meta_max_concentration: float = 0.4
    meta_cluster_caps: Dict[str, float] = field(default_factory=dict)
    meta_dd_deleveraging_curve: Dict[str, float] = field(default_factory=dict)
