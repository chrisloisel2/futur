"""
src/institutional/data/dataset_builder.py
─────────────────────────────────────────────────────────────────────────────
Constructeur de datasets par moteur.

Chaque moteur a ses propres :
    - actifs
    - timeframe
    - familles de features
    - familles de labels
    - horizons

Les datasets sont construits SÉPARÉMENT par moteur — jamais mixés.

MOTEURS :
    BTC_ETH_TREND    : BTC/ETH, 1h/4h, momentum/vol/funding/basis, labels 24h-7d
    TRM_EVENT        : SOL/BNB/alts, 1h, CVD/OI/funding/liquidations, labels 1h-8h
    CARRY            : multi-actifs, 1h, funding/basis/OI/mark, labels 8h-72h
    CROSS_SECTIONAL  : univers liquide, 1h, ranks, labels rank 24h-72h
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

from src.institutional.data.loaders import (
    load_asset_1h, load_funding, load_metrics,
)
from src.institutional.data.asof_join import build_master_frame
from src.institutional.data.data_quality import DataQualityChecker
from src.institutional.features.returns import compute_return_features
from src.institutional.features.volatility import (
    compute_volatility_features, realized_vol,
)
from src.institutional.features.technical import compute_trend_features
from src.institutional.features.derivatives import (
    compute_funding_features, compute_oi_features, compute_basis_features,
)
from src.institutional.labels.trend_labels import build_btc_eth_labels
from src.institutional.labels.event_labels import build_trm_event_labels
from src.institutional.labels.carry_labels import build_carry_labels

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional"


# ─── Configuration par moteur ────────────────────────────────────────────────

@dataclass
class EngineDatasetConfig:
    """Configuration complète d'un dataset moteur."""
    engine_name: str          # "BTC_ETH_TREND", "TRM_EVENT", "CARRY", "CROSS_SECTIONAL"
    assets: List[str]
    start: str
    end: str

    # Features
    feature_families: List[str]   # ["returns", "volatility", "trend", "derivatives"]

    # Labels
    label_family: str             # "trend" | "event" | "carry" | "cross_sectional"
    label_horizons_h: List[int]
    label_k: float = 1.0
    label_cost_bps: float = 10.0

    # Timeframe principal
    timeframe: str = "1h"

    # Sources optionnelles
    include_funding: bool = True
    include_oi: bool = True
    include_spot_basis: bool = True

    # Output
    save_path: Optional[str] = None


def btc_eth_trend_config(
    start: str = "2021-01-01",
    end: str = "2025-12-31",
) -> EngineDatasetConfig:
    """
    Configuration officielle pour BTC/ETH Trend Following.

    Features : momentum multi-horizons, vol, Donchian, funding, basis, OI
    Labels   : trend_cont_24h, trend_cont_72h, trend_cont_168h
    k=1.0    : ~60% FLAT, 20% UP, 20% DOWN pour BTC 24h
    """
    return EngineDatasetConfig(
        engine_name="BTC_ETH_TREND",
        assets=["BTCUSDT", "ETHUSDT"],
        start=start,
        end=end,
        feature_families=["returns", "volatility", "trend", "derivatives"],
        label_family="trend",
        label_horizons_h=[24, 72, 168],
        label_k=1.0,
        label_cost_bps=10.0,
        include_funding=True,
        include_oi=True,
    )


def trm_event_config(
    start: str = "2021-01-01",
    end: str = "2025-12-31",
) -> EngineDatasetConfig:
    """
    Configuration pour TRM EVENT ENGINE (alts).

    Features : CVD, OI, funding, base court-terme
    Labels   : event_cont_1h, event_cont_4h, event_cont_8h
    k=1.5    : seuils plus hauts pour anomalies
    """
    return EngineDatasetConfig(
        engine_name="TRM_EVENT",
        assets=["SOLUSDT", "BNBUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT",
                "XRPUSDT", "ADAUSDT"],
        start=start,
        end=end,
        feature_families=["returns", "volatility", "derivatives"],
        label_family="event",
        label_horizons_h=[1, 4, 8],
        label_k=1.5,
        label_cost_bps=15.0,
        include_funding=True,
        include_oi=True,
    )


def carry_config(
    start: str = "2021-01-01",
    end: str = "2025-12-31",
) -> EngineDatasetConfig:
    return EngineDatasetConfig(
        engine_name="CARRY",
        assets=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
                "AVAXUSDT", "DOGEUSDT", "LINKUSDT"],
        start=start,
        end=end,
        feature_families=["derivatives"],
        label_family="carry",
        label_horizons_h=[8, 24, 72],
        label_k=1.0,
        label_cost_bps=10.0,
        include_funding=True,
        include_oi=True,
    )


# ─── Builder principal ────────────────────────────────────────────────────────

class EngineDatasetBuilder:
    """
    Construit les datasets par moteur avec features et labels corrects.

    Usage :
        builder  = EngineDatasetBuilder()
        config   = btc_eth_trend_config(start="2021-01-01", end="2025-12-31")
        datasets = builder.build(config)
        # datasets = {"BTCUSDT": DataFrame, "ETHUSDT": DataFrame}
    """

    def build(
        self,
        config: EngineDatasetConfig,
        validate_quality: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        Construit les datasets pour tous les actifs du moteur.
        Retourne {asset: DataFrame(features + labels)}.
        """
        logger.info(f"[{config.engine_name}] Building dataset {config.start}→{config.end}")
        logger.info(f"  Assets  : {config.assets}")
        logger.info(f"  Labels  : {config.label_family} horizons={config.label_horizons_h} k={config.label_k}")

        results: Dict[str, pd.DataFrame] = {}

        for asset in config.assets:
            try:
                df = self._build_asset(config, asset, validate_quality)
                results[asset] = df
                logger.info(f"  {asset}: {len(df)} barres × {len(df.columns)} colonnes")
                self._log_label_distribution(df, config)
            except Exception as e:
                logger.error(f"  {asset}: FAILED — {e}")

        return results

    def _build_asset(
        self,
        config: EngineDatasetConfig,
        asset: str,
        validate_quality: bool,
    ) -> pd.DataFrame:
        """Pipeline complet pour un actif."""

        # ── 1. OHLCV ──────────────────────────────────────────────────────────
        ohlcv = load_asset_1h(asset, config.start, config.end)

        if validate_quality:
            checker = DataQualityChecker(ohlcv, asset=asset, source="futures", timeframe="1h")
            report  = checker.run()
            if not report.is_valid:
                raise ValueError(f"Data quality failed: {report.issues}")

        # ── 2. Sources supplémentaires (funding, OI) ──────────────────────────
        funding = None
        if config.include_funding:
            try:
                funding = load_funding(config.start, config.end)
            except FileNotFoundError:
                logger.warning(f"  {asset}: funding non disponible")

        metrics = None
        if config.include_oi:
            try:
                metrics = load_metrics(config.start, config.end, resample_to="1H")
            except FileNotFoundError:
                logger.warning(f"  {asset}: metrics (OI/LSR) non disponibles")

        # ── 3. Master frame (jointures causales) ──────────────────────────────
        master = build_master_frame(
            ohlcv_1h=ohlcv,
            funding=funding,
            metrics=metrics,
        )

        # ── 4. Features ───────────────────────────────────────────────────────
        feature_parts = []

        if "returns" in config.feature_families:
            feature_parts.append(compute_return_features(master))

        if "volatility" in config.feature_families:
            feature_parts.append(compute_volatility_features(master))

        if "trend" in config.feature_families:
            feature_parts.append(compute_trend_features(master))

        if "derivatives" in config.feature_families:
            for fn in [compute_funding_features, compute_oi_features, compute_basis_features]:
                part = fn(master)
                if not part.empty:
                    feature_parts.append(part)

        if not feature_parts:
            raise ValueError(f"Aucune feature calculée pour {asset}")

        features = pd.concat(feature_parts, axis=1)

        # ── 5. Vol annualisée (nécessaire pour les labels) ────────────────────
        vol_annual = realized_vol(master["close"], window=24, annualize=True)

        # ── 6. Labels ─────────────────────────────────────────────────────────
        if config.label_family == "trend":
            labels = build_btc_eth_labels(
                master["close"], vol_annual,
                horizons_h=config.label_horizons_h,
                k=config.label_k,
                cost_bps=config.label_cost_bps,
            )

        elif config.label_family == "event":
            labels = build_trm_event_labels(
                master["close"], vol_annual,
                horizons_h=config.label_horizons_h,
                k=config.label_k,
                cost_bps=config.label_cost_bps,
            )

        elif config.label_family == "carry":
            if "funding_rate" not in master.columns:
                raise ValueError(f"{asset}: funding_rate obligatoire pour carry labels")
            labels = build_carry_labels(
                master["close"],
                master["funding_rate"],
                vol_annual,
                horizons_h=config.label_horizons_h,
                cost_bps=config.label_cost_bps,
            )

        else:
            raise ValueError(f"label_family={config.label_family!r} non supportée")

        # ── 7. Assemblage ─────────────────────────────────────────────────────
        features["asset"]          = asset
        features["engine"]         = config.engine_name
        features["vol_annual"]     = vol_annual

        dataset = pd.concat([features, labels], axis=1)

        # Supprimer warmup (premières barres avec NaN excessifs)
        min_non_nan = 0.5
        valid_mask  = dataset[list(features.columns)].notna().mean(axis=1) >= min_non_nan
        dataset = dataset[valid_mask]

        return dataset

    def save(
        self,
        datasets: Dict[str, pd.DataFrame],
        config: EngineDatasetConfig,
    ) -> Dict[str, Path]:
        """Sauvegarde les datasets en parquet par actif."""
        save_root = Path(config.save_path) if config.save_path else (
            ARTIFACTS_ROOT / "datasets" / config.engine_name.lower()
        )
        save_root.mkdir(parents=True, exist_ok=True)

        paths: Dict[str, Path] = {}
        for asset, df in datasets.items():
            path = save_root / f"{asset}_{config.start[:4]}_{config.end[:4]}.parquet"
            df.to_parquet(path)
            paths[asset] = path

        # Sauvegarder le config
        meta = {
            "engine_name": config.engine_name,
            "assets": config.assets,
            "start": config.start,
            "end": config.end,
            "label_family": config.label_family,
            "label_horizons_h": config.label_horizons_h,
            "label_k": config.label_k,
            "label_cost_bps": config.label_cost_bps,
            "feature_families": config.feature_families,
            "n_assets_built": len(datasets),
        }
        (save_root / "config.json").write_text(json.dumps(meta, indent=2))
        logger.info(f"  Saved to {save_root}")
        return paths

    def load(
        self,
        engine_name: str,
        asset: str,
        start: str,
        end: str,
        root: Optional[Path] = None,
    ) -> pd.DataFrame:
        root = root or (ARTIFACTS_ROOT / "datasets" / engine_name.lower())
        path = root / f"{asset}_{start[:4]}_{end[:4]}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Dataset {engine_name}/{asset} non trouvé : {path}")
        return pd.read_parquet(path)

    @staticmethod
    def _log_label_distribution(
        df: pd.DataFrame,
        config: EngineDatasetConfig,
    ) -> None:
        """Log la distribution des labels pour vérifier."""
        for h in config.label_horizons_h:
            col_candidates = [
                f"trend_cont_{h}h",
                f"event_cont_{h}h",
                f"carry_net_{h}h",
            ]
            for col in col_candidates:
                if col in df.columns:
                    valid = df[col].dropna()
                    n = len(valid)
                    if n > 0:
                        up   = (valid == 1).sum() / n
                        down = (valid == -1).sum() / n
                        flat = (valid == 0).sum() / n
                        logger.info(
                            f"    {col}: UP={up:.1%} DOWN={down:.1%} FLAT={flat:.1%} n={n}"
                        )
