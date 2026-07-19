"""
src/institutional/engines/ml_engine.py
─────────────────────────────────────────────────────────────────────────────
Moteur ML générique (walk-forward) — base partagée de Pullback / Liquidation /
Carry. Chaque moteur concret = un jeu de features + une fonction de label + des
seuils. Évite de dupliquer train/infer dans 4 dossiers.

Anti-leakage : modèles PAR FOLD (expanding window). Pour l'année Y on entraîne
model_Y sur < Y → prédiction OOS. 2026 utilise model_2025 (train ≤2024).

Tous les moteurs émettent des Opportunity LONG (long-only). Le différenciateur
est la SOURCE D'EDGE (features + label + horizon), pas la direction.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import Opportunity, ReasonCode
from src.institutional.engines.base import AlphaEngine, EngineConfig
from src.institutional.engines.legacy_bridge import ROOT, annualized_vol_from_rv, load_enriched
from src.institutional.portfolio.zones import classify_zone

logger = logging.getLogger(__name__)

ENGINES_ROOT = ROOT / "artifacts" / "institutional" / "engines"
FOLD_YEARS = (2022, 2023, 2024, 2025)
FOLD_PLAN = {y: {"train_end": f"{y-1}-12-31"} for y in FOLD_YEARS}


@dataclass
class MLEngineSpec:
    engine_id: str
    assets: List[str]
    feature_cols: List[str]
    label_fn: Callable[[pd.DataFrame, float, float], pd.Series]  # (df, horizon_h, cost)->{0,1}
    horizon_hours: float = 8.0
    tau_a: float = 0.58
    tau_b: float = 0.50
    cost_bps: float = 10.0
    expected_move: float = 0.03      # mouvement favorable typique (pour expected_return)
    max_position_fraction: float = 0.25
    status: str = "SHADOW"
    extra_cols: List[str] = field(default_factory=list)  # colonnes pour le label (close…)


class MLSignalEngine(AlphaEngine):
    def __init__(self, spec: MLEngineSpec, version: str = "v1.0"):
        super().__init__(EngineConfig(
            engine_id=spec.engine_id, status=spec.status, horizon_hours=spec.horizon_hours,
            cost_bps=spec.cost_bps, assets=list(spec.assets),
            max_position_fraction=spec.max_position_fraction,
        ))
        self.spec = spec
        self.version = version
        self._models: Dict[tuple, object] = {}

    def thresholds_for(self, asset: str):
        return self.spec.tau_a, self.spec.tau_b

    def _model_path(self, asset: str, year: int) -> Path:
        return ENGINES_ROOT / self.engine_id / asset / self.version / f"model_{year}.pkl"

    # ── préparation données ────────────────────────────────────────────────────
    def _prepare(self, asset: str, start: str, end: str) -> Optional[pd.DataFrame]:
        cols = list(set(self.spec.feature_cols) | set(self.spec.extra_cols) | {"close"})
        df = load_enriched(asset, required_cols=cols, start=start, end=end)
        if df is None or df.empty:
            return None
        df = df.set_index("datetime").sort_index()
        feats = [c for c in self.spec.feature_cols if c in df.columns]
        if len(feats) < 3:
            logger.warning("[%s] %s: features insuffisantes (%d)", self.engine_id, asset, len(feats))
            return None
        df["_label"] = self.spec.label_fn(df, self.spec.horizon_hours, self.cost_fraction)
        df["_feats"] = None  # marqueur
        df.attrs["feats"] = feats
        return df

    # ── entraînement walk-forward ──────────────────────────────────────────────
    def train(self, start: str = "2021-01-01", end: str = "2025-12-31",
              n_estimators: int = 300) -> Dict:
        import lightgbm as lgb
        report = {"engine": self.engine_id, "assets": {}}
        for asset in self.assets:
            df = self._prepare(asset, start, end)
            if df is None:
                continue
            feats = df.attrs["feats"]
            df = df.dropna(subset=feats + ["_label"])
            asset_rep = []
            for year in FOLD_YEARS:
                tr = df[df.index < pd.Timestamp(FOLD_PLAN[year]["train_end"], tz="UTC")]
                te = df[df.index.year == year]
                if len(tr) < 500 or len(te) < 100 or tr["_label"].nunique() < 2:
                    continue
                clf = lgb.LGBMClassifier(
                    n_estimators=n_estimators, learning_rate=0.03, num_leaves=31,
                    subsample=0.8, colsample_bytree=0.7, min_child_samples=50,
                    reg_lambda=1.0, random_state=42, n_jobs=-1, verbose=-1,
                )
                clf.fit(tr[feats], tr["_label"].astype(int))
                p_te = clf.predict_proba(te[feats])[:, 1]
                try:
                    from sklearn.metrics import roc_auc_score
                    auc = float(roc_auc_score(te["_label"].astype(int), p_te)) if te["_label"].nunique() > 1 else 0.5
                except Exception:
                    auc = 0.5
                path = self._model_path(asset, year)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "wb") as f:
                    pickle.dump({"model": clf, "features": feats}, f)
                asset_rep.append({"year": year, "auc": round(auc, 4),
                                  "n_train": len(tr), "prevalence": round(float(te["_label"].mean()), 4)})
                logger.info("[%s] %s fold %d: AUC=%.3f n_train=%d prev=%.1f%%",
                            self.engine_id, asset, year, auc, len(tr), 100 * te["_label"].mean())
            report["assets"][asset] = asset_rep
        return report

    def _load_model(self, asset: str, year: int):
        ref_year = year if year in FOLD_YEARS else max(FOLD_YEARS)
        key = (asset, ref_year)
        if key in self._models:
            return self._models[key]
        path = self._model_path(asset, ref_year)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                obj = pickle.load(f)
            self._models[key] = obj
            return obj
        except Exception as e:
            logger.warning("[%s] load model %s/%s échec: %s", self.engine_id, asset, ref_year, e)
            return None

    # ── inférence ──────────────────────────────────────────────────────────────
    def generate(self, asset: str, start: str, end: str) -> List[Opportunity]:
        df = self._prepare(asset, start, end)
        if df is None:
            return []
        feats = df.attrs["feats"]
        cost = self.cost_fraction
        rv_col = "realized_volatility_20" if "realized_volatility_20" in df.columns else None
        opps: List[Opportunity] = []

        years = sorted(set(df.index.year))
        for year in years:
            rows = df[df.index.year == year]
            obj = self._load_model(asset, year)
            if obj is None:
                continue
            model, mfeats = obj["model"], obj["features"]
            use = [f for f in mfeats if f in rows.columns]
            if len(use) < 3:
                continue
            try:
                p = model.predict_proba(rows[use].fillna(0.0))[:, 1]
            except Exception as e:
                logger.warning("[%s] predict %s/%s échec: %s", self.engine_id, asset, year, e)
                continue
            for i, (ts, row) in enumerate(rows.iterrows()):
                pi = float(p[i])
                zone, reason = classify_zone(pi, self.spec.tau_a, self.spec.tau_b)
                direction = "LONG" if zone != "C_REJECT" else "CASH"
                er = max(0.0, (pi - self.spec.tau_b)) / max(1.0 - self.spec.tau_b, 1e-6) * self.spec.expected_move
                rv = float(row[rv_col]) if rv_col and pd.notna(row.get(rv_col)) else None
                opps.append(Opportunity(
                    timestamp=ts, engine_id=self.engine_id, asset=asset, direction=direction,
                    status=self.status, p_success=pi, expected_return=er,
                    expected_vol=annualized_vol_from_rv(rv),
                    expected_holding_hours=self.horizon_hours, expected_cost=cost,
                    score_raw=pi, score_net=er - cost,
                    confidence=float(min(1.0, abs(pi - 0.5) * 2.0)),
                    regime=str(row.get("vol_regime", "UNKNOWN")),
                    correlation_bucket=self.bucket(asset),
                    max_position_fraction=self.config.max_position_fraction,
                    stop_loss=0.025, take_profit=self.spec.expected_move,
                    decision_zone=zone, reason=reason.value,
                ))
        logger.info("[%s] %s: %d opportunités (%s→%s)", self.engine_id, asset, len(opps), start, end)
        return opps
