from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import numpy as np
import tensorflow as tf


@dataclass(frozen=True)
class MetaScalerConfig:
    d_model: int = 64
    n_layers: int = 3
    dropout: float = 0.1
    min_scale: float = 0.0
    max_scale: float = 1.0

    # Decision thresholds
    min_trade_scale: float = 0.15
    min_abs_edge_final: float = 0.05

    # Safety clamps for inputs (optional but recommended)
    clamp_entropy: Tuple[float, float] = (0.0, 5.0)
    clamp_roi: Tuple[float, float] = (-1.0, 1.0)


def _finite(x: np.ndarray, default: float = 0.0) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = np.where(np.isfinite(x), x, np.float32(default))
    return x


def pack_meta_inputs(
    tradeability: np.ndarray,        # [B,1]
    regime_confidence: np.ndarray,   # [B,1]
    regime_entropy: np.ndarray,      # [B,1]
    pairwise_consistency: np.ndarray,# [B,1]
    recent_roi: np.ndarray,          # [B,1]
    cfg: Optional[MetaScalerConfig] = None,
) -> np.ndarray:
    tradeability = _finite(tradeability)
    regime_confidence = _finite(regime_confidence)
    regime_entropy = _finite(regime_entropy)
    pairwise_consistency = _finite(pairwise_consistency)
    recent_roi = _finite(recent_roi)

    if cfg is not None:
        lo, hi = cfg.clamp_entropy
        regime_entropy = np.clip(regime_entropy, lo, hi)
        lo, hi = cfg.clamp_roi
        recent_roi = np.clip(recent_roi, lo, hi)

    return np.concatenate(
        [tradeability, regime_confidence, regime_entropy, pairwise_consistency, recent_roi],
        axis=-1,
    ).astype(np.float32)


class MetaScaler(tf.keras.Model):
    def __init__(self, input_dim: int, cfg: MetaScalerConfig):
        super().__init__(name="meta_scaler")
        self.cfg = cfg
        self.in_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        blocks = []
        for _ in range(cfg.n_layers):
            blocks += [
                tf.keras.layers.Dense(cfg.d_model, activation="gelu"),
                tf.keras.layers.Dropout(cfg.dropout),
                tf.keras.layers.LayerNormalization(epsilon=1e-6),
            ]
        self.backbone = tf.keras.Sequential(blocks)
        self.head = tf.keras.layers.Dense(1, activation="sigmoid", name="scale_head")

        # build shape sanity
        self.build((None, input_dim))

    def call(self, x, training=False) -> tf.Tensor:
        x = self.in_ln(x)
        h = self.backbone(x, training=training)
        scale = self.head(h)
        return tf.clip_by_value(scale, self.cfg.min_scale, self.cfg.max_scale)


class MetaDecider:
    def __init__(self, model: MetaScaler, cfg: MetaScalerConfig):
        self.model = model
        self.cfg = cfg

    def decide(self, inputs_vec: np.ndarray, edge_raw: np.ndarray) -> Dict[str, np.ndarray]:
        x = tf.convert_to_tensor(_finite(inputs_vec), tf.float32)
        scale = self.model(x, training=False).numpy()          # [B,1]
        scale_1d = scale[:, 0]                                  # [B]

        e = _finite(edge_raw)
        e_1d = e.reshape(-1)                                    # [B]

        edge_final = e_1d * scale_1d                            # [B]

        trade = (scale_1d >= self.cfg.min_trade_scale) & (np.abs(edge_final) >= self.cfg.min_abs_edge_final)

        return {
            "scale": scale_1d,
            "edge_final": edge_final,
            "trade": trade,
        }
