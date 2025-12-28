from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from domain.state.targets import TargetPosition


@dataclass
class ExposureSnapshot:
    gross_usd: float
    net_usd: float
    cluster_exposure: Dict[str, float]


class ExposureEngine:
    def compute(self, targets: List[TargetPosition], clusters: Dict[str, str]) -> ExposureSnapshot:
        gross = 0.0
        net = 0.0
        cluster_exp: Dict[str, float] = {}
        for t in targets:
            gross += abs(t.notional_usd)
            direction = 1 if t.side == "LONG" else -1
            net += t.notional_usd * direction
            cluster = clusters.get(t.symbol, "default")
            cluster_exp[cluster] = cluster_exp.get(cluster, 0.0) + abs(t.notional_usd)
        return ExposureSnapshot(gross_usd=gross, net_usd=net, cluster_exposure=cluster_exp)
