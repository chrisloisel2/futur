"""
src/institutional/labels/label_store.py
─────────────────────────────────────────────────────────────────────────────
Label Store institutionnel — versionnement et persistance des labels.

Chaque label set est versionné et ne peut pas être modifié après création.
Le versioning inclut un hash des paramètres pour garantir la reproductibilité.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.institutional.labels.forward_returns import compute_all_labels
from src.institutional.labels.triple_barrier import (
    TripleBarrierConfig, compute_triple_barrier_labels,
)
from src.institutional.features.volatility import realized_vol

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path(__file__).parents[3] / "artifacts" / "institutional" / "labels"


class LabelStore:
    """
    Construit et persiste les labels pour un actif donné.

    Usage
    -----
    ls = LabelStore(version="v1.0")
    labels = ls.build(close=close_series, vol=vol_series, asset="BTCUSDT")
    ls.save(labels, asset="BTCUSDT")

    IMPORTANT : les labels sont construits APRÈS les features mais
    les DataFrames labels et features NE SONT JAMAIS combinés avant le split.
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
            "horizons_h": [4, 12, 24, 72, 168],
            "cost_bps": 10.0,
            "vol_threshold_k": 0.5,
            "triple_barrier": {
                "max_bars": 72,
                "k_up": 1.0,
                "k_down": 1.0,
                "vol_window": 24,
                "cost_bps": 10.0,
            },
        }

    def _config_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.config, sort_keys=True).encode()
        ).hexdigest()[:12]

    def build(
        self,
        close: pd.Series,
        asset: str,
        vol_series: Optional[pd.Series] = None,
        funding_rate: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Construit l'ensemble complet des labels pour un actif.

        Paramètres
        ----------
        close        : prix de clôture 1h
        asset        : nom de l'actif
        vol_series   : vol réalisée précalculée (si None, calculée ici)
        funding_rate : funding rate 8h (pour carry labels)

        Retourne
        --------
        DataFrame labels (index = même que close)
        """
        logger.info(f"[LabelStore] Building labels for {asset} (version={self.version})")

        # Calculer vol si pas fournie
        # IMPORTANT : vol non-annualisée pour les barrières triple barrier
        if vol_series is None:
            vol_series = realized_vol(
                close,
                window=self.config["triple_barrier"]["vol_window"],
                annualize=False,   # barrières en fraction brute, pas annualisée
            )

        # 1. Forward returns + classification + carry
        labels = compute_all_labels(
            close=close,
            vol_series=vol_series,
            horizons=self.config["horizons_h"],
            funding_rate=funding_rate,
            cost_bps=self.config["cost_bps"],
        )

        # 2. Triple barrier
        tb_config = TripleBarrierConfig(**self.config["triple_barrier"])
        tb_labels = compute_triple_barrier_labels(
            close=close,
            config=tb_config,
            vol_series=vol_series,
        )
        # Préfixer pour éviter les conflits de noms
        tb_labels = tb_labels.rename(columns={
            "label": "tb_label",
            "time_to_bar": "tb_time",
            "realized_ret": "tb_ret",
            "touched_upper": "tb_upper",
            "touched_lower": "tb_lower",
            "censored": "tb_censored",
        })
        labels = pd.concat([labels, tb_labels[["tb_label", "tb_time", "tb_ret", "tb_censored"]]], axis=1)

        # Métadonnées
        labels["asset"] = asset
        labels["label_version"] = self.version
        labels["config_hash"] = self._config_hash()

        n_total = len(labels)
        n_valid = labels["tb_label"].notna().sum()
        logger.info(f"  Labels: {n_total} barres, {n_valid} valides")
        logger.info(f"  Triple barrier: {(labels['tb_label']==1).sum()} UP / "
                    f"{(labels['tb_label']==-1).sum()} DOWN / "
                    f"{(labels['tb_label']==0).sum()} FLAT/CENSORED")

        return labels

    def save(self, labels: pd.DataFrame, asset: str) -> Path:
        path = self.artifacts_root / self.version / f"{asset}_labels.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        labels.to_parquet(path)

        meta = {
            "version": self.version,
            "asset": asset,
            "config": self.config,
            "config_hash": self._config_hash(),
            "n_rows": len(labels),
            "label_columns": [c for c in labels.columns if "label" in c or "ret" in c],
            "start": str(labels.index.min()),
            "end": str(labels.index.max()),
            "class_distribution": {
                col: labels[col].value_counts().to_dict()
                for col in labels.columns
                if col.endswith("_label") or col == "tb_label"
            },
        }
        meta_path = path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2, default=str))

        logger.info(f"  Saved: {path}")
        return path

    def load(self, asset: str) -> pd.DataFrame:
        path = self.artifacts_root / self.version / f"{asset}_labels.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Labels non trouvés : {path}")
        return pd.read_parquet(path)

    def exists(self, asset: str) -> bool:
        return (self.artifacts_root / self.version / f"{asset}_labels.parquet").exists()

    def prepare_dataset(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        target_col: str,
        drop_label_cols: bool = True,
        embargo_bars: int = 5,
    ) -> tuple:
        """
        Assemble features + labels en évitant tout lookahead.

        Le join est fait sur l'index timestamp.
        Les colonnes de labels NE SONT PAS dans X — uniquement dans y.
        L'embargo appliqué ici est un décalage supplémentaire si nécessaire.

        Retourne (X, y) prêts pour entraînement.
        """
        label_cols = [c for c in labels.columns
                      if any(k in c for k in ["label", "fwd_ret", "tb_"])]

        # Join sur l'index
        dataset = features.join(labels[[target_col]], how="inner")
        dataset = dataset.dropna(subset=[target_col])

        # Supprimer les barres sans features suffisantes
        feature_cols = [c for c in dataset.columns if c != target_col and c not in label_cols]
        X = dataset[feature_cols].drop(
            columns=["asset", "feature_version", "label_version", "config_hash"],
            errors="ignore",
        )
        y = dataset[target_col]

        return X, y
