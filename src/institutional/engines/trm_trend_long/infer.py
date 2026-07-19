"""
src/institutional/engines/trm_trend_long/infer.py
─────────────────────────────────────────────────────────────────────────────
Moteur TRM_TREND_LONG — wrapper de l'alpha prouvé (TRM Fleet Long).

On NE touche PAS le cœur TRM. On charge le fleet persisté, on appelle
.predict() en batch, puis on enveloppe chaque barre dans une Opportunity avec
zones A/B/C, reason codes, expected_return / expected_holding / coût.

Status par défaut : PAPER (l'alpha principal n'est jamais mis "live" ici).
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import Opportunity, ReasonCode
from src.institutional.engines.base import AlphaEngine, EngineConfig
from src.institutional.engines.legacy_bridge import (
    TRAIN_END_BOUNDARY,
    annualized_vol_from_rv,
    compute_regime_long,
    load_enriched,
    load_return_predictor,
    load_trm_fleet,
)
from src.institutional.portfolio.zones import classify_zone, get_thresholds

logger = logging.getLogger(__name__)

DEFAULT_ASSETS = ["BTCUSDT", "ETHUSDT"]


class TRMTrendLongEngine(AlphaEngine):
    """Moteur trend long principal — wrappe le TRM Fleet persisté."""

    def __init__(
        self,
        status: str = "PAPER",
        assets: Optional[List[str]] = None,
        horizon_hours: float = 8.0,
        cost_bps: float = 10.0,
        max_position_fraction: float = 0.25,
        engine_id: str = "TRM_TREND_LONG",
        enforce_oos: bool = True,
    ):
        super().__init__(EngineConfig(
            engine_id=engine_id,
            status=status,
            horizon_hours=horizon_hours,
            cost_bps=cost_bps,
            assets=assets or list(DEFAULT_ASSETS),
            max_position_fraction=max_position_fraction,
        ))
        self.enforce_oos = enforce_oos

    def generate(self, asset: str, start: str, end: str) -> List[Opportunity]:
        fleet = load_trm_fleet(asset)
        if fleet is None:
            logger.warning("[%s] pas de fleet pour %s → aucune opportunité", self.engine_id, asset)
            return []

        # anti-leakage : les fleets sont entraînés ≤2025 → on clampe au OOS (≥2026)
        if self.enforce_oos and pd.Timestamp(start, tz="UTC") < TRAIN_END_BOUNDARY:
            if pd.Timestamp(end, tz="UTC") < TRAIN_END_BOUNDARY:
                logger.warning(
                    "[%s] %s: fenêtre [%s,%s] entièrement IN-SAMPLE (≤2025) → rien émis.",
                    self.engine_id, asset, start, end,
                )
                return []
            logger.warning(
                "[%s] %s: start clampé %s → %s (anti-leakage, fleet train ≤2025).",
                self.engine_id, asset, start, TRAIN_END_BOUNDARY.date(),
            )
            start = TRAIN_END_BOUNDARY.isoformat()

        feats = list(getattr(fleet, "features", []) or [])
        df = load_enriched(asset, required_cols=feats, start=start, end=end)
        if df is None or df.empty:
            return []

        # prédiction batch (cœur TRM intact)
        mask = np.ones(len(df), dtype=bool)
        try:
            p = np.asarray(fleet.predict(df, mask), dtype=float)
        except Exception as e:
            logger.warning("[%s] predict %s échec: %s", self.engine_id, asset, e)
            return []
        p = np.clip(np.nan_to_num(p, nan=0.5), 0.0, 1.0)

        regime = compute_regime_long(df)

        # expected_return : ReturnPredictor si dispo, sinon proxy monotone
        ret_pred = load_return_predictor(asset)
        exp_ret = None
        if ret_pred is not None:
            try:
                exp_ret = np.asarray(ret_pred.predict_return(df), dtype=float)
            except Exception:
                exp_ret = None

        thr = get_thresholds(asset)
        cost = self.cost_fraction
        rv_col = "rv_24" if "rv_24" in df.columns else None

        opps: List[Opportunity] = []
        for i in range(len(df)):
            ts = df["datetime"].iloc[i]
            reg = str(regime.iloc[i]) if i < len(regime) else "UNKNOWN"
            pi = float(p[i])

            # gate NO_LONG maintenue : régime bear → rejet explicite
            if reg == "NO_LONG":
                zone, reason, direction = "C_REJECT", ReasonCode.REJECT_BEAR_NO_LONG, "CASH"
            else:
                zone, reason = classify_zone(pi, thr.tau_a, thr.tau_b)
                direction = "LONG" if zone != "C_REJECT" else "CASH"

            er = float(exp_ret[i]) if exp_ret is not None else max(0.0, (pi - 0.5)) * 0.08
            rv = float(df[rv_col].iloc[i]) if rv_col else None
            opp = Opportunity(
                timestamp=ts,
                engine_id=self.engine_id,
                asset=asset,
                direction=direction,
                status=self.status,
                p_success=pi,
                expected_return=er,
                expected_vol=annualized_vol_from_rv(rv),
                expected_holding_hours=self.horizon_hours,
                expected_cost=cost,
                score_raw=pi,
                score_net=er - cost,
                confidence=float(min(1.0, abs(pi - 0.5) * 2.0)),
                regime=reg,
                correlation_bucket=self.bucket(asset),
                max_position_fraction=self.config.max_position_fraction,
                stop_loss=0.025,
                take_profit=0.04,
                decision_zone=zone,
                reason=reason.value if hasattr(reason, "value") else str(reason),
            )
            opps.append(opp)
        logger.info("[%s] %s: %d opportunités (%s→%s)", self.engine_id, asset, len(opps), start, end)
        return opps
