"""
CROSS_SECTIONAL_LONG — ranking top-k (diversification, fréquence ↑).

Ne dit pas "SOL va monter" mais "SOL est meilleur que BTC/ETH/BNB/… sur la
prochaine fenêtre". Pas de mélange des labels BTC/alts : on classe une mesure
de force relative (momentum_score) en cross-section, par barre, sur l'univers.

V1 : ranking heuristique sur momentum_score (causal, calculé sur le passé) —
pas de modèle ML par actif (évite la contamination BTC/alt). On prend le top-k
si la force relative dépasse un seuil. Gate : top-k PF≥1.30, hit@1 > random+15%.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import Opportunity, ReasonCode
from src.institutional.engines.base import AlphaEngine, EngineConfig
from src.institutional.engines.legacy_bridge import annualized_vol_from_rv, load_enriched
from src.institutional.portfolio.zones import classify_zone

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
                    "AVAXUSDT", "LINKUSDT", "XRPUSDT", "ADAUSDT"]
SCORE_COL = "momentum_score"


class CrossSectionalLongEngine(AlphaEngine):
    def __init__(
        self,
        status: str = "SHADOW",
        universe: Optional[List[str]] = None,
        top_k: int = 3,
        horizon_hours: float = 24.0,
        tau_a: float = 0.80,   # percentile cross-section
        tau_b: float = 0.60,
        cost_bps: float = 10.0,
        expected_move: float = 0.03,
        engine_id: str = "CROSS_SECTIONAL_LONG",
    ):
        super().__init__(EngineConfig(
            engine_id=engine_id, status=status, horizon_hours=horizon_hours,
            cost_bps=cost_bps, assets=universe or list(DEFAULT_UNIVERSE),
            max_position_fraction=0.25,
        ))
        self.top_k = top_k
        self.tau_a = tau_a
        self.tau_b = tau_b
        self.expected_move = expected_move
        self._cache_key = None
        self._cache: Dict[str, List[Opportunity]] = {}

    def thresholds_for(self, asset: str):
        return self.tau_a, self.tau_b

    def _build(self, start: str, end: str) -> None:
        key = (start, end)
        if self._cache_key == key:
            return
        scores, vols = {}, {}
        for a in self.assets:
            df = load_enriched(a, required_cols=[SCORE_COL, "close", "realized_volatility_20"],
                               start=start, end=end)
            if df is None or df.empty or SCORE_COL not in df.columns:
                continue
            df = df.set_index("datetime").sort_index()
            scores[a] = df[SCORE_COL]
            vols[a] = df.get("realized_volatility_20")
        if len(scores) < 3:
            self._cache_key, self._cache = key, {}
            return
        panel = pd.DataFrame(scores).dropna(how="all")
        # rang cross-sectional par barre → percentile [0,1]
        rank = panel.rank(axis=1, pct=True)
        # top-k par barre
        order = panel.rank(axis=1, ascending=False, method="first")

        out: Dict[str, List[Opportunity]] = {a: [] for a in scores}
        cost = self.cost_fraction
        for a in scores:
            r = rank[a]
            o = order[a]
            v = vols.get(a)
            for ts in panel.index:
                pct = r.get(ts, np.nan)
                if pd.isna(pct):
                    continue
                in_topk = o.get(ts, 999) <= self.top_k
                zone, reason = classify_zone(float(pct), self.tau_a, self.tau_b)
                # A_TRADE seulement si dans le top-k ET percentile élevé
                if zone == "A_TRADE" and not in_topk:
                    zone, reason = "B_SHADOW", ReasonCode.ACCEPT_SHADOW
                direction = "LONG" if zone != "C_REJECT" else "CASH"
                er = max(0.0, (float(pct) - self.tau_b)) / max(1 - self.tau_b, 1e-6) * self.expected_move
                rv = float(v.get(ts)) if v is not None and pd.notna(v.get(ts)) else None
                out[a].append(Opportunity(
                    timestamp=ts, engine_id=self.engine_id, asset=a, direction=direction,
                    status=self.status, p_success=float(pct), expected_return=er,
                    expected_vol=annualized_vol_from_rv(rv),
                    expected_holding_hours=self.horizon_hours, expected_cost=cost,
                    score_raw=float(panel[a].get(ts, 0.0)), score_net=er - cost,
                    confidence=float(abs(pct - 0.5) * 2.0),
                    regime="XS", correlation_bucket=self.bucket(a),
                    max_position_fraction=self.config.max_position_fraction,
                    stop_loss=0.03, take_profit=self.expected_move,
                    decision_zone=zone, reason=reason.value if hasattr(reason, "value") else str(reason),
                ))
        self._cache_key, self._cache = key, out

    def generate(self, asset: str, start: str, end: str) -> List[Opportunity]:
        self._build(start, end)
        return self._cache.get(asset, [])
