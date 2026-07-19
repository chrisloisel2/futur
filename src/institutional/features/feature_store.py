"""
src/institutional/features/feature_store.py
─────────────────────────────────────────────────────────────────────────────
Feature Store institutionnel.

Orchestrateur principal de la construction des features.
Garanties :
  1. Causalité : aucune donnée future utilisée
  2. Versioning : chaque feature set est hashé et versionné
  3. Reproductibilité : les paramètres sont sauvegardés avec les features
  4. Cache parquet : recalcul uniquement si paramètres changent
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.institutional.data.loaders import (
    load_asset_1h, load_funding, load_metrics,
)
from src.institutional.data.asof_join import build_master_frame
from src.institutional.data.data_quality import DataQualityChecker, assert_all_valid
from src.institutional.features.returns import compute_return_features
from src.institutional.features.volatility import compute_volatility_features
from src.institutional.features.technical import compute_trend_features
from src.institutional.features.derivatives import (
    compute_funding_features, compute_oi_features, compute_basis_features,
)

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional" / "features"


class FeatureStore:
    """
    Construit et persiste le feature set institutionnel pour un ou plusieurs actifs.

    Usage
    -----
    fs = FeatureStore(version="v1.0")
    features = fs.build(
        asset="BTCUSDT",
        start="2021-01-01",
        end="2026-05-30",
    )
    fs.save(features, asset="BTCUSDT")
    """

    def __init__(
        self,
        version: str = "v1.0",
        artifacts_root: Optional[Path] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.version = version
        self.artifacts_root = Path(artifacts_root or ARTIFACTS_ROOT)
        self.config = config or self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        return {
            "return_horizons_h": [1, 4, 8, 12, 24, 48, 72, 168],
            "vol_windows_h": [15, 24, 60, 120, 240, 720],
            "ema_spans": [8, 21, 55, 144],
            "max_stale_funding_h": 10.0,
            "max_stale_metrics_h": 2.0,
        }

    def _version_hash(self) -> str:
        config_str = json.dumps(self.config, sort_keys=True)
        return hashlib.sha256(f"{self.version}{config_str}".encode()).hexdigest()[:12]

    def build(
        self,
        asset: str,
        start: str,
        end: str,
        include_funding: bool = True,
        include_metrics: bool = True,
        include_spot: bool = False,
        validate_quality: bool = True,
    ) -> pd.DataFrame:
        """
        Construit le feature set complet pour un actif.

        Paramètres
        ----------
        asset   : "BTCUSDT", "ETHUSDT", etc.
        start   : date de début inclusive (format "YYYY-MM-DD")
        end     : date de fin inclusive
        validate_quality : si True, lève une exception si les données sont invalides

        Retourne
        --------
        DataFrame features (index = DatetimeIndex UTC, 1h)
        Colonnes = toutes les features (pas les OHLCV bruts)
        """
        logger.info(f"[FeatureStore] Building {asset} {start}:{end} (version={self.version})")

        # ── 1. Chargement données brutes ──────────────────────────────────────
        ohlcv = load_asset_1h(asset, start, end)
        logger.info(f"  OHLCV: {len(ohlcv)} barres 1h")

        funding = None
        if include_funding:
            try:
                funding = load_funding(start, end)
                logger.info(f"  Funding: {len(funding)} points")
            except FileNotFoundError:
                logger.warning(f"  Funding non disponible pour {asset}")

        metrics = None
        if include_metrics:
            try:
                metrics = load_metrics(start, end, resample_to="1H")
                logger.info(f"  Metrics (OI/LSR): {len(metrics)} points")
            except FileNotFoundError:
                logger.warning(f"  Metrics non disponibles pour {asset}")

        # ── 2. Data quality ───────────────────────────────────────────────────
        if validate_quality:
            checker = DataQualityChecker(ohlcv, asset=asset, source="futures", timeframe="1h")
            report = checker.run()
            if not report.is_valid:
                raise ValueError(f"Données invalides pour {asset} :\n{report.issues}")
            logger.info(report.summary())

        # ── 3. Master frame (jointures causales) ──────────────────────────────
        master = build_master_frame(
            ohlcv_1h=ohlcv,
            funding=funding,
            metrics=metrics,
        )

        # ── 4. Calcul des features ─────────────────────────────────────────────
        feature_parts = []

        # Returns
        ret_feats = compute_return_features(
            master,
            horizons_h=self.config["return_horizons_h"],
        )
        feature_parts.append(ret_feats)

        # Volatilité
        vol_feats = compute_volatility_features(
            master,
            windows=self.config["vol_windows_h"],
        )
        feature_parts.append(vol_feats)

        # Trend / Momentum
        trend_feats = compute_trend_features(
            master,
            ema_spans=self.config["ema_spans"],
        )
        feature_parts.append(trend_feats)

        # Derivatives
        funding_feats = compute_funding_features(master)
        if not funding_feats.empty:
            feature_parts.append(funding_feats)

        oi_feats = compute_oi_features(master)
        if not oi_feats.empty:
            feature_parts.append(oi_feats)

        basis_feats = compute_basis_features(master)
        if not basis_feats.empty:
            feature_parts.append(basis_feats)

        # ── 5. Assemblage ─────────────────────────────────────────────────────
        features = pd.concat(feature_parts, axis=1)
        features["asset"] = asset
        features["feature_version"] = self.version

        # Supprimer les lignes avec trop de NaN au début (warmup)
        min_non_nan = 0.5
        mask = features.notna().mean(axis=1) >= min_non_nan
        features = features[mask]

        logger.info(
            f"  Features: {len(features)} barres × {len(features.columns)} colonnes"
        )
        return features

    def build_multi(
        self,
        assets: List[str],
        start: str,
        end: str,
        **kwargs: Any,
    ) -> Dict[str, pd.DataFrame]:
        """Construit les features pour plusieurs actifs."""
        result = {}
        for asset in assets:
            try:
                result[asset] = self.build(asset, start, end, **kwargs)
            except Exception as e:
                logger.error(f"[FeatureStore] {asset} failed: {e}")
        return result

    def save(self, features: pd.DataFrame, asset: str) -> Path:
        """Sauvegarde les features en parquet partitionné."""
        path = self.artifacts_root / self.version / f"{asset}_features.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        features.to_parquet(path)

        # Sauvegarder les métadonnées
        meta = {
            "version": self.version,
            "asset": asset,
            "config": self.config,
            "version_hash": self._version_hash(),
            "n_rows": len(features),
            "n_cols": len(features.columns),
            "columns": list(features.columns),
            "start": str(features.index.min()),
            "end": str(features.index.max()),
        }
        meta_path = path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2))

        logger.info(f"  Saved: {path}")
        return path

    def load(self, asset: str) -> pd.DataFrame:
        """Charge les features depuis le cache parquet."""
        path = self.artifacts_root / self.version / f"{asset}_features.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Features non trouvées : {path}")
        return pd.read_parquet(path)

    def exists(self, asset: str) -> bool:
        path = self.artifacts_root / self.version / f"{asset}_features.parquet"
        return path.exists()
