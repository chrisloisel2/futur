"""
src/institutional/engines/trm_trend_inst/infer.py
─────────────────────────────────────────────────────────────────────────────
Moteur TRM_TREND_INST — moteur trend institutionnel (shadow parallèle).

Source d'edge : btc_eth_trend (LightGBM/Logistic, label trend_cont_24h, 3 classes
{-1,0,1}). Modèles par fold (walk-forward strict) : pour l'année Y, model_Y est
entraîné sur < Y → prédiction OOS propre.

Tourne en STATUS SHADOW : sert de comparaison/validation croisée au TRM legacy.
Écrit dans le MÊME DecisionLedger. Couverture : 2022-2025 (folds institutionnels).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import Opportunity, ReasonCode
from src.institutional.engines.base import AlphaEngine, EngineConfig
from src.institutional.engines.legacy_bridge import ROOT, annualized_vol_from_rv
from src.institutional.portfolio.zones import classify_zone

logger = logging.getLogger(__name__)

DEFAULT_ASSETS = ["BTCUSDT", "ETHUSDT"]
FOLD_YEARS = (2022, 2023, 2024, 2025)
MODELS_ROOT = ROOT / "artifacts" / "institutional" / "backtests" / "btc_eth_trend"


class TRMTrendInstEngine(AlphaEngine):
    """Moteur trend institutionnel (shadow), modèles par fold OOS 2022-2025."""

    def __init__(
        self,
        status: str = "SHADOW",
        assets: Optional[List[str]] = None,
        horizon_hours: float = 24.0,
        cost_bps: float = 10.0,
        max_position_fraction: float = 0.25,
        engine_id: str = "TRM_TREND_INST",
        version: str = "v1.0",
        tau_a: float = 0.45,
        tau_b: float = 0.33,
    ):
        super().__init__(EngineConfig(
            engine_id=engine_id, status=status, horizon_hours=horizon_hours,
            cost_bps=cost_bps, assets=assets or list(DEFAULT_ASSETS),
            max_position_fraction=max_position_fraction,
        ))
        self.version = version
        # Le modèle 3-classes a une P(up) calibrée ~15% (médiane 0.16, max ~0.64) :
        # ses seuils sont propres au moteur, distincts du p_long binaire du TRM legacy.
        self.tau_a = tau_a
        self.tau_b = tau_b
        self._dataset_cache: dict = {}

    def thresholds_for(self, asset: str):
        return self.tau_a, self.tau_b

    def _load_dataset(self, asset: str) -> Optional[pd.DataFrame]:
        if asset in self._dataset_cache:
            return self._dataset_cache[asset]
        from src.institutional.data.dataset_builder import EngineDatasetBuilder
        try:
            df = EngineDatasetBuilder().load("BTC_ETH_TREND", asset, "2021-01-01", "2025-12-31")
        except FileNotFoundError:
            logger.warning("[%s] dataset institutionnel absent pour %s", self.engine_id, asset)
            return None
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        self._dataset_cache[asset] = df
        return df

    def _load_fold_model(self, asset: str, year: int):
        from src.institutional.models.base import InstitutionalModel
        path = MODELS_ROOT / asset / self.version / str(year) / f"model_{year}.pkl"
        if not path.exists():
            return None
        try:
            return InstitutionalModel.load(path)
        except Exception as e:
            logger.warning("[%s] load model %s/%s échec: %s", self.engine_id, asset, year, e)
            return None

    @staticmethod
    def _p_up(model, X: pd.DataFrame) -> np.ndarray:
        proba = np.asarray(model.predict_proba(X), dtype=float)
        if proba.ndim == 1:
            return np.clip(proba, 0.0, 1.0)
        classes = list(getattr(model, "_classes", getattr(model, "classes_", [])))
        col = classes.index(1) if 1 in classes else proba.shape[1] - 1
        return np.clip(proba[:, col], 0.0, 1.0)

    def generate(self, asset: str, start: str, end: str) -> List[Opportunity]:
        df = self._load_dataset(asset)
        if df is None or df.empty:
            return []
        t0, t1 = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        win = df[(df.index >= t0) & (df.index <= t1)]
        if win.empty:
            return []

        cost = self.cost_fraction
        vol_col = "ewma_vol_24h" if "ewma_vol_24h" in win.columns else None
        opps: List[Opportunity] = []

        for year in FOLD_YEARS:
            yr_rows = win[win.index.year == year]
            if yr_rows.empty:
                continue
            model = self._load_fold_model(asset, year)
            if model is None:
                continue
            feats = [c for c in (getattr(model, "_feature_names", []) or []) if c in yr_rows.columns]
            if not feats:
                continue
            try:
                p = self._p_up(model, yr_rows[feats])
            except Exception as e:
                logger.warning("[%s] predict %s/%s échec: %s", self.engine_id, asset, year, e)
                continue

            for i, (ts, row) in enumerate(yr_rows.iterrows()):
                pi = float(p[i])
                zone, reason = classify_zone(pi, self.tau_a, self.tau_b)
                direction = "LONG" if zone != "C_REJECT" else "CASH"
                rv = float(row[vol_col]) if vol_col and pd.notna(row.get(vol_col)) else None
                regime = str(row.get("vol_regime", "UNKNOWN"))
                # expected_return relatif au seuil propre du moteur (p calibrée ~15%)
                er = max(0.0, (pi - self.tau_b)) * 0.06
                opps.append(Opportunity(
                    timestamp=ts, engine_id=self.engine_id, asset=asset, direction=direction,
                    status=self.status, p_success=pi,
                    expected_return=er,
                    expected_vol=annualized_vol_from_rv(rv),
                    expected_holding_hours=self.horizon_hours, expected_cost=cost,
                    score_raw=pi, score_net=er - cost,
                    confidence=float(min(1.0, abs(pi - 0.5) * 2.0)),
                    regime=regime, correlation_bucket=self.bucket(asset),
                    max_position_fraction=self.config.max_position_fraction,
                    stop_loss=0.03, take_profit=0.05, decision_zone=zone,
                    reason=reason.value,
                ))
        logger.info("[%s] %s: %d opportunités OOS (%s→%s)", self.engine_id, asset, len(opps), start, end)
        return opps
