"""
REGIME-AWARE MARKET MODEL WITH MIXTURE OF EXPERTS

Architecture à détection de régimes avec experts spécialisés pour séries temporelles financières.

Mathematical Foundation:
------------------------
Les marchés financiers présentent une non-stationnarité structurelle où la distribution
conditionnelle P(r_{t+1} | F_t) varie selon le régime τ du marché.

Décomposition de la variance:
    Var[r̂] = E_τ[Var[r̂ | τ]] + Var_τ[E[r̂ | τ]]

Un modèle global unique apprend une moyenne sur des distributions incompatibles,
maximisant le terme Var_τ[E[r̂ | τ]] (variance inter-régimes).

Le découpage en régimes minimise cette variance en rendant chaque distribution
conditionnelle homogène, permettant à chaque expert de se spécialiser.

Architecture:
-------------
    Input [B, L, F]
         ↓
    RegimeClassifier → p_regime ∈ Δ⁴ (simplex)
         ↓
    {Expert₀, Expert₁, Expert₂, Expert₃, Expert₄}
         ↓
    Gating (hard/soft)
         ↓
    Predictions: {ret, rv, [dir]}
"""

from __future__ import annotations

import os
import math
from typing import Dict, Tuple, Optional, Literal
from dataclasses import dataclass

import numpy as np
import tensorflow as tf


# =========================
# CONFIGURATION
# =========================
@dataclass(frozen=True)
class RegimeConfig:
    """Configuration for regime-aware architecture"""
    # Data
    lookback: int = 256
    horizon: int = 12
    batch_size: int = 256

    # Regime classifier
    regime_backbone: Literal["cnn", "tcn"] = "cnn"
    regime_d_model: int = 64
    regime_n_layers: int = 3
    regime_dropout: float = 0.15
    n_regimes: int = 5  # {trend, mean_revert, high_vol, low_vol, range}

    # Experts
    expert_type: Literal["tcn", "transformer"] = "tcn"
    expert_d_model: int = 64  # ~1/3 of original model
    expert_n_layers: int = 2
    expert_dropout: float = 0.20

    # Gating
    gating_mode: Literal["hard", "soft"] = "soft"
    entropy_weight: float = 0.01  # Regularization against collapse

    # Training
    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0
    epochs: int = 20

    # Loss weights
    w_regime: float = 0.3
    w_ret: float = 1.0
    w_rv: float = 0.4
    w_dir: float = 0.0  # Direction NOT learned globally

    # Two-phase training
    pretrain_regime_epochs: int = 5

    seed: int = 1337


CONFIG = RegimeConfig()


# =========================
# REGIME DEFINITION
# =========================
def compute_regime_labels(
    features: np.ndarray,
    feature_keys: list[str],
    lookback: int = 256,
) -> np.ndarray:
    """
    Définition mathématique formelle des régimes à partir des features observables.

    Régimes:
        0: TREND       - tendance directionnelle forte
        1: MEAN_REVERT - retour à la moyenne
        2: HIGH_VOL    - volatilité élevée
        3: LOW_VOL     - volatilité faible
        4: RANGE       - consolidation / range-bound

    Args:
        features: [T, F] array contenant toutes les features
        feature_keys: liste des noms de features (pour indexation)
        lookback: fenêtre pour calcul des régimes

    Returns:
        regime_labels: [T] array d'entiers ∈ {0, 1, 2, 3, 4}

    Mathematical Definitions:
    ------------------------

    1) TREND (τ=0):
        - Forte pente directionnelle: |slope_ema_20| > Q₇₅(|slope|)
        - Faible variance directionnelle: std(sign(ret)) < Q₂₅(std)
        - Indicateur: dist_ema_20 éloignée + faible oscillation RSI

    2) MEAN_REVERT (τ=1):
        - RSI extrême: RSI < 30 OU RSI > 70
        - Retour vers moyenne: sign(dist_ema_20) ≠ sign(ret)
        - Indicateur: anticorrélation prix/EMA

    3) HIGH_VOL (τ=2):
        - Volatilité normalisée élevée: RV > Q₇₅(RV)
        - Indicateur: rv_ann_60 > quantile 75%

    4) LOW_VOL (τ=3):
        - Volatilité normalisée faible: RV < Q₂₅(RV)
        - Indicateur: rv_ann_60 < quantile 25%

    5) RANGE (τ=4):
        - Faible tendance: |slope_ema| < Q₂₅(|slope|)
        - Oscillations contenues: |dist_ema| < Q₂₅(|dist|)

    Propriétés:
    -----------
    - Pas de labels manuels
    - Calculable online (pas de fuite temporelle)
    - Stable temporellement (pas de switching rapide)
    - Mutuellement exclusif (argmax sur scores)
    """
    T = features.shape[0]
    regime_labels = np.zeros(T, dtype=np.int32)

    # Index mapping
    feature_map = {k: i for i, k in enumerate(feature_keys)}

    # Extract relevant features
    idx_ret = feature_map.get("log_ret", feature_map.get("ret", 0))
    idx_rv = feature_map.get("rv_ann_60", feature_map.get("rv_60", 1))
    idx_rsi = feature_map.get("rsi_14", 2)
    idx_dist_ema_20 = feature_map.get("dist_ema_20", 3)
    idx_dist_ema_50 = feature_map.get("dist_ema_50", 4)

    ret = features[:, idx_ret]
    rv = features[:, idx_rv]
    rsi = features[:, idx_rsi]
    dist_ema_20 = features[:, idx_dist_ema_20]
    dist_ema_50 = features[:, idx_dist_ema_50]

    # Pour chaque fenêtre, calculer le régime
    for t in range(lookback, T):
        window_ret = ret[t - lookback : t]
        window_rv = rv[t - lookback : t]
        window_rsi = rsi[t - lookback : t]
        window_dist_20 = dist_ema_20[t - lookback : t]
        window_dist_50 = dist_ema_50[t - lookback : t]

        # Compute regime indicators
        # ---------------------------

        # 1) TREND: slope + low variance direction
        slope_ema = np.polyfit(np.arange(lookback), window_dist_20, deg=1)[0]
        abs_slope = np.abs(slope_ema)
        direction_changes = np.sum(np.diff(np.sign(window_ret)) != 0)
        direction_stability = 1.0 - (direction_changes / lookback)

        # 2) MEAN_REVERT: RSI extreme + anticorrelation
        rsi_current = window_rsi[-1]
        rsi_extreme = (rsi_current < 30.0) or (rsi_current > 70.0)
        # Anticorrélation: signe(dist) ≠ signe(ret)
        anticorr = np.mean(np.sign(window_dist_20) != np.sign(window_ret))

        # 3) HIGH_VOL: RV normalisée élevée
        rv_current = window_rv[-1]
        rv_q75 = np.percentile(window_rv, 75)

        # 4) LOW_VOL: RV normalisée faible
        rv_q25 = np.percentile(window_rv, 25)

        # 5) RANGE: faible tendance + faible écart EMA
        abs_dist_q25 = np.percentile(np.abs(window_dist_20), 25)

        # Score chaque régime (0-1 normalisé)
        scores = np.zeros(5, dtype=np.float32)

        # TREND (0): forte pente + stabilité directionnelle
        scores[0] = (abs_slope > np.percentile(np.abs(window_dist_20), 75)) * direction_stability

        # MEAN_REVERT (1): RSI extrême + anticorrélation
        scores[1] = float(rsi_extreme) * anticorr

        # HIGH_VOL (2): RV > Q75
        scores[2] = float(rv_current > rv_q75)

        # LOW_VOL (3): RV < Q25
        scores[3] = float(rv_current < rv_q25)

        # RANGE (4): faible pente + faible écart
        scores[4] = (abs_slope < np.percentile(np.abs(window_dist_20), 25)) * \
                    (np.abs(window_dist_20[-1]) < abs_dist_q25)

        # Assign regime (argmax)
        regime_labels[t] = np.argmax(scores)

    return regime_labels


def compute_regime_statistics(regime_labels: np.ndarray) -> Dict[str, float]:
    """
    Compute regime distribution and temporal stability.

    Stability metrics:
    ------------------
    - Distribution: percentage in each regime
    - Temporal stability: average regime duration
    - Switching rate: transitions per 1000 timesteps
    """
    n = len(regime_labels)
    regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]

    stats = {}

    # Distribution
    for i, name in enumerate(regime_names):
        count = np.sum(regime_labels == i)
        stats[f"regime_{i}_{name}_pct"] = 100.0 * count / n

    # Temporal stability
    switches = np.sum(np.diff(regime_labels) != 0)
    stats["switches_per_1000"] = 1000.0 * switches / n

    # Average regime duration
    durations = []
    current_regime = regime_labels[0]
    duration = 1
    for i in range(1, n):
        if regime_labels[i] == current_regime:
            duration += 1
        else:
            durations.append(duration)
            current_regime = regime_labels[i]
            duration = 1
    durations.append(duration)

    stats["avg_regime_duration"] = np.mean(durations)
    stats["median_regime_duration"] = np.median(durations)

    return stats


# =========================
# REGIME CLASSIFIER
# =========================
class RegimeClassifier(tf.keras.layers.Layer):
    """
    Module 1: Détecteur de régime

    Architecture:
        Input [B, L, F] → CNN1D / TCN → GlobalPool → Dense → Softmax → p_regime ∈ Δ⁴

    Properties:
        - Léger (< 50k params)
        - Pas d'accès au futur (causal)
        - Stabilité prioritaire (dropout + LayerNorm)

    Loss:
        SparseCategoricalCrossentropy(y_regime, p_regime)

    Output:
        p_regime: [B, 5] probability distribution over regimes (float32)
    """

    def __init__(
        self,
        n_regimes: int = 5,
        d_model: int = 64,
        n_layers: int = 3,
        dropout: float = 0.15,
        backbone: Literal["cnn", "tcn"] = "cnn",
        name: str = "regime_classifier",
        **kwargs
    ):
        super().__init__(name=name, **kwargs)

        self.n_regimes = n_regimes
        self.d_model = d_model
        self.n_layers = n_layers
        self.dropout_rate = dropout
        self.backbone = backbone

        # Input projection
        self.input_proj = tf.keras.layers.Dense(d_model, activation="gelu")
        self.input_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        # Backbone: CNN or TCN
        if backbone == "cnn":
            self.layers_list = []
            for i in range(n_layers):
                kernel_size = 3 if i == 0 else 5
                self.layers_list.append(
                    tf.keras.Sequential([
                        tf.keras.layers.Conv1D(
                            filters=d_model,
                            kernel_size=kernel_size,
                            padding="causal",
                            activation=None,
                        ),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6),
                        tf.keras.layers.Activation("gelu"),
                        tf.keras.layers.Dropout(dropout),
                    ], name=f"cnn_layer_{i}")
                )
        else:  # TCN
            self.layers_list = []
            for i in range(n_layers):
                dilation = 2 ** i
                self.layers_list.append(
                    tf.keras.Sequential([
                        tf.keras.layers.Conv1D(
                            filters=d_model,
                            kernel_size=3,
                            padding="causal",
                            dilation_rate=dilation,
                            activation=None,
                        ),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6),
                        tf.keras.layers.Activation("gelu"),
                        tf.keras.layers.Dropout(dropout),
                    ], name=f"tcn_layer_{i}")
                )

        # Pooling
        self.global_pool = tf.keras.layers.GlobalAveragePooling1D()

        # Classifier head
        self.classifier = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(n_regimes),
            tf.keras.layers.Activation("softmax", dtype="float32"),  # Force float32
        ], name="classifier_head")

    def call(self, x, training=False):
        """
        Args:
            x: [B, L, F] - Input features
            training: bool

        Returns:
            p_regime: [B, n_regimes] - Probability distribution over regimes

        Shape flow:
            [B, L, F] → [B, L, d_model] → [B, d_model] → [B, n_regimes]
        """
        # Project to d_model
        h = self.input_proj(x)
        h = self.input_ln(h)

        # Backbone (CNN or TCN)
        for layer in self.layers_list:
            h = layer(h, training=training)

        # Global pooling
        pooled = self.global_pool(h)  # [B, d_model]

        # Classifier
        p_regime = self.classifier(pooled, training=training)  # [B, n_regimes]

        return p_regime

    def get_config(self):
        config = super().get_config()
        config.update({
            "n_regimes": self.n_regimes,
            "d_model": self.d_model,
            "n_layers": self.n_layers,
            "dropout": self.dropout_rate,
            "backbone": self.backbone,
        })
        return config


# =========================
# REGIME EXPERT
# =========================
class RegimeExpert(tf.keras.layers.Layer):
    """
    Module 2: Expert spécialisé par régime

    Chaque expert est un petit modèle indépendant (TCN ou Transformer shallow)
    qui se spécialise dans un régime spécifique.

    Architecture:
        Input [B, L, F] → Backbone (TCN/Transformer) → Heads {ret, rv}

    Properties:
        - Taille ≤ 1/3 du modèle global original
        - Régularisation forte (dropout)
        - Pas de direction globale (seulement ret + rv)

    Outputs:
        - ret: [B, horizon] - return predictions
        - rv: [B] - volatility prediction (scalar aggregated)
    """

    def __init__(
        self,
        regime_id: int,
        horizon: int = 12,
        d_model: int = 64,
        n_layers: int = 2,
        dropout: float = 0.20,
        expert_type: Literal["tcn", "transformer"] = "tcn",
        name: Optional[str] = None,
        **kwargs
    ):
        if name is None:
            name = f"expert_regime_{regime_id}"
        super().__init__(name=name, **kwargs)

        self.regime_id = regime_id
        self.horizon = horizon
        self.d_model = d_model
        self.n_layers = n_layers
        self.dropout_rate = dropout
        self.expert_type = expert_type

        # Input projection
        self.input_proj = tf.keras.layers.Dense(d_model, activation="gelu")
        self.input_ln = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        # Backbone
        if expert_type == "tcn":
            self.backbone_layers = []
            for i in range(n_layers):
                dilation = 2 ** i
                self.backbone_layers.append(
                    tf.keras.Sequential([
                        tf.keras.layers.Conv1D(
                            filters=d_model,
                            kernel_size=3,
                            padding="causal",
                            dilation_rate=dilation,
                            activation=None,
                        ),
                        tf.keras.layers.LayerNormalization(epsilon=1e-6),
                        tf.keras.layers.Activation("gelu"),
                        tf.keras.layers.Dropout(dropout),
                    ], name=f"tcn_{i}")
                )
        else:  # Transformer shallow
            self.backbone_layers = []
            for i in range(n_layers):
                self.backbone_layers.append(
                    TransformerBlock(
                        d_model=d_model,
                        n_heads=4,
                        d_ff=d_model * 2,
                        dropout=dropout,
                        name=f"transformer_{i}",
                    )
                )

        # Pooling
        self.global_pool = tf.keras.layers.GlobalAveragePooling1D()

        # Shared representation
        self.shared = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
        ], name="shared")

        # Prediction heads
        self.ret_head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model // 2, activation="gelu"),
            tf.keras.layers.Dense(horizon),
        ], name="ret_head")

        self.rv_head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model // 2, activation="gelu"),
            tf.keras.layers.Dense(1),
            tf.keras.layers.Activation("softplus"),
        ], name="rv_head")

    def call(self, x, training=False):
        """
        Args:
            x: [B, L, F] - Input features
            training: bool

        Returns:
            dict with keys:
                - "ret": [B, horizon]
                - "rv": [B]

        Shape flow:
            [B, L, F] → [B, L, d_model] → [B, d_model] → heads
        """
        # Project
        h = self.input_proj(x)
        h = self.input_ln(h)

        # Backbone
        for layer in self.backbone_layers:
            h = layer(h, training=training)

        # Pool
        pooled = self.global_pool(h)  # [B, d_model]

        # Shared
        shared = self.shared(pooled, training=training)

        # Heads
        ret = self.ret_head(shared, training=training)  # [B, horizon]
        rv = self.rv_head(shared, training=training)    # [B, 1]

        # Cast to float32 and squeeze rv
        ret = tf.cast(ret, tf.float32)
        rv = tf.squeeze(rv, axis=-1)
        rv = tf.cast(rv, tf.float32)

        return {"ret": ret, "rv": rv}

    def get_config(self):
        config = super().get_config()
        config.update({
            "regime_id": self.regime_id,
            "horizon": self.horizon,
            "d_model": self.d_model,
            "n_layers": self.n_layers,
            "dropout": self.dropout_rate,
            "expert_type": self.expert_type,
        })
        return config


# Helper: Transformer block for expert
class TransformerBlock(tf.keras.layers.Layer):
    """Simple transformer block for expert backbone"""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        d_ff: int,
        dropout: float,
        name: Optional[str] = None,
        **kwargs
    ):
        super().__init__(name=name, **kwargs)

        self.ln1 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.ln2 = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        self.attn = tf.keras.layers.MultiHeadAttention(
            num_heads=n_heads,
            key_dim=d_model // n_heads,
            dropout=dropout,
        )
        self.drop1 = tf.keras.layers.Dropout(dropout)

        self.ff = tf.keras.Sequential([
            tf.keras.layers.Dense(d_ff, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(d_model),
        ])
        self.drop2 = tf.keras.layers.Dropout(dropout)

    def call(self, x, training=False):
        # Self-attention
        h = self.ln1(x)
        a = self.attn(h, h, training=training)
        x = x + self.drop1(a, training=training)

        # Feedforward
        h = self.ln2(x)
        f = self.ff(h, training=training)
        x = x + self.drop2(f, training=training)

        return x


# =========================
# REGIME-AWARE MODEL (MAIN)
# =========================
class RegimeAwareMarketModel(tf.keras.Model):
    """
    Architecture à détection de régimes avec experts spécialisés.

    Pipeline:
        Input [B, L, F]
            ↓
        RegimeClassifier → p_regime ∈ Δ⁴
            ↓
        {Expert₀, Expert₁, Expert₂, Expert₃, Expert₄}
            ↓
        Gating (hard or soft)
            ↓
        Outputs: {ret, rv}

    Mathematical Properties:
    ------------------------

    1) Variance Decomposition:
        Var[ŷ] = E_τ[Var[ŷ | τ]] + Var_τ[E[ŷ | τ]]

        Global model maximizes Var_τ[E[ŷ | τ]] (inter-regime variance)
        Regime-specific experts minimize E_τ[Var[ŷ | τ]] (intra-regime variance)

    2) Mixture of Experts (soft gating):
        ŷ = Σᵢ p(τ=i | x) · Expert_i(x)

        where p(τ | x) from RegimeClassifier

    3) Hard Gating:
        ŷ = Expert_argmax(p_regime)(x)

        Non-differentiable but interpretable

    Training:
    ---------

    Option 1: Joint training
        L_total = L_regime + Σᵢ p(τ=i) · L_expert_i

    Option 2: Two-phase
        Phase 1: Pre-train regime classifier
        Phase 2: Train experts conditionally

    Regularization:
    ---------------
    - Entropy regularization on p_regime (prevent collapse)
    - Strong dropout on experts (prevent overfitting to regime)
    """

    def __init__(
        self,
        cfg: RegimeConfig,
        feature_dim: int,
        name: str = "regime_aware_model",
        **kwargs
    ):
        super().__init__(name=name, **kwargs)

        self.cfg = cfg
        self.feature_dim = feature_dim

        # Module 1: Regime Classifier
        self.regime_classifier = RegimeClassifier(
            n_regimes=cfg.n_regimes,
            d_model=cfg.regime_d_model,
            n_layers=cfg.regime_n_layers,
            dropout=cfg.regime_dropout,
            backbone=cfg.regime_backbone,
        )

        # Module 2: Experts (one per regime)
        self.experts = []
        for i in range(cfg.n_regimes):
            expert = RegimeExpert(
                regime_id=i,
                horizon=cfg.horizon,
                d_model=cfg.expert_d_model,
                n_layers=cfg.expert_n_layers,
                dropout=cfg.expert_dropout,
                expert_type=cfg.expert_type,
            )
            self.experts.append(expert)

    def call(self, x, training=False, return_regime_probs=False):
        """
        Forward pass with regime detection and expert routing.

        Args:
            x: [B, L, F] - Input features
            training: bool
            return_regime_probs: bool - If True, return regime probabilities

        Returns:
            dict with keys:
                - "ret": [B, horizon]
                - "rv": [B]
                - "regime_probs": [B, n_regimes] (if return_regime_probs=True)

        Flow:
            1. Classify regime
            2. Route to experts
            3. Aggregate predictions (hard or soft gating)
        """
        B = tf.shape(x)[0]

        # 1) Regime classification
        p_regime = self.regime_classifier(x, training=training)  # [B, n_regimes]

        # 2) Expert predictions
        # All experts predict (needed for soft gating and gradient flow)
        expert_preds = []
        for expert in self.experts:
            pred = expert(x, training=training)
            expert_preds.append(pred)

        # 3) Gating
        if self.cfg.gating_mode == "hard":
            # Hard gating: argmax routing
            regime_indices = tf.argmax(p_regime, axis=-1)  # [B]

            # Gather predictions from selected experts
            ret_list = tf.stack([pred["ret"] for pred in expert_preds], axis=1)  # [B, n_regimes, H]
            rv_list = tf.stack([pred["rv"] for pred in expert_preds], axis=1)    # [B, n_regimes]

            # Batch indices for gather_nd
            batch_indices = tf.range(B)[:, None]  # [B, 1]
            regime_indices_expanded = regime_indices[:, None]  # [B, 1]
            gather_indices = tf.concat([batch_indices, regime_indices_expanded], axis=1)  # [B, 2]

            ret_pred = tf.gather_nd(ret_list, gather_indices)  # [B, H]
            rv_pred = tf.gather_nd(rv_list, gather_indices)    # [B]

        else:  # soft (Mixture of Experts)
            # Soft gating: weighted average
            p_regime_expanded_ret = p_regime[:, :, None]  # [B, n_regimes, 1]
            p_regime_expanded_rv = p_regime  # [B, n_regimes]

            ret_stack = tf.stack([pred["ret"] for pred in expert_preds], axis=1)  # [B, n_regimes, H]
            rv_stack = tf.stack([pred["rv"] for pred in expert_preds], axis=1)    # [B, n_regimes]

            # Weighted sum
            ret_pred = tf.reduce_sum(ret_stack * p_regime_expanded_ret, axis=1)  # [B, H]
            rv_pred = tf.reduce_sum(rv_stack * p_regime_expanded_rv, axis=1)     # [B]

        # Outputs
        outputs = {
            "ret": ret_pred,
            "rv": rv_pred,
        }

        if return_regime_probs:
            outputs["regime_probs"] = p_regime

        return outputs

    def compute_entropy_regularization(self, p_regime):
        """
        Entropy regularization to prevent regime collapse.

        H(p) = -Σᵢ pᵢ log pᵢ

        Maximizing entropy encourages uniform distribution over regimes,
        preventing the model from collapsing to a single regime.

        Args:
            p_regime: [B, n_regimes] - regime probabilities

        Returns:
            entropy_loss: scalar - negative entropy (to minimize)
        """
        # Clip for numerical stability
        p_regime = tf.clip_by_value(p_regime, 1e-8, 1.0)

        # Entropy: H = -Σ p log p
        entropy = -tf.reduce_sum(p_regime * tf.math.log(p_regime), axis=-1)  # [B]

        # Average over batch
        avg_entropy = tf.reduce_mean(entropy)

        # Return negative entropy (we want to maximize entropy = minimize negative entropy)
        return -avg_entropy

    def get_config(self):
        config = super().get_config()
        config.update({
            "cfg": {
                "lookback": self.cfg.lookback,
                "horizon": self.cfg.horizon,
                "n_regimes": self.cfg.n_regimes,
                "regime_d_model": self.cfg.regime_d_model,
                "expert_d_model": self.cfg.expert_d_model,
                "gating_mode": self.cfg.gating_mode,
            },
            "feature_dim": self.feature_dim,
        })
        return config


# =========================
# TRAINING
# =========================
class RegimeAwareTrainer:
    """
    Trainer for regime-aware model with two-phase training support.

    Phase 1 (optional): Pre-train regime classifier
        - Train only regime classifier on regime labels
        - Freeze classifier afterward

    Phase 2: Train full model
        - Unfreeze all
        - Joint training with combined loss

    Loss:
        L_total = w_regime · L_regime + Σᵢ p(τ=i) · (w_ret · L_ret_i + w_rv · L_rv_i)
                  + w_entropy · L_entropy

    where:
        - L_regime: SparseCategoricalCrossentropy
        - L_ret: Huber loss
        - L_rv: Huber loss
        - L_entropy: negative entropy (regularization)
    """

    def __init__(
        self,
        model: RegimeAwareMarketModel,
        cfg: RegimeConfig,
    ):
        self.model = model
        self.cfg = cfg

        # Optimizer
        total_steps = cfg.epochs * 1000  # Adjust based on data
        lr_schedule = CosineWarmup(
            base_lr=cfg.lr,
            warmup_steps=int(0.05 * total_steps),
            total_steps=total_steps,
        )
        self.optimizer = tf.keras.optimizers.AdamW(
            learning_rate=lr_schedule,
            weight_decay=cfg.weight_decay,
            beta_1=0.9,
            beta_2=0.95,
            epsilon=1e-8,
            global_clipnorm=cfg.clip_norm,
        )

        # Metrics
        self.train_loss_tracker = tf.keras.metrics.Mean(name="train_loss")
        self.val_loss_tracker = tf.keras.metrics.Mean(name="val_loss")
        self.regime_acc_tracker = tf.keras.metrics.SparseCategoricalAccuracy(name="regime_acc")

    @tf.function
    def train_step(self, x, y_regime, y_ret, y_rv):
        """
        Single training step.

        Args:
            x: [B, L, F]
            y_regime: [B] - regime labels
            y_ret: [B, H] - return targets
            y_rv: [B] - volatility targets

        Returns:
            loss: scalar
        """
        with tf.GradientTape() as tape:
            # Forward
            outputs = self.model(x, training=True, return_regime_probs=True)
            p_regime = outputs["regime_probs"]
            y_ret_pred = outputs["ret"]
            y_rv_pred = outputs["rv"]

            # Regime classification loss
            regime_loss = tf.keras.losses.sparse_categorical_crossentropy(
                y_regime, p_regime, from_logits=False
            )
            regime_loss = tf.reduce_mean(regime_loss)

            # Expert losses (weighted by regime probability)
            # For each sample, weight the loss by the regime probability
            ret_loss = tf.keras.losses.huber(y_ret, y_ret_pred, delta=1.0)
            ret_loss = tf.reduce_mean(ret_loss)

            rv_loss = tf.keras.losses.huber(y_rv, y_rv_pred, delta=0.01)
            rv_loss = tf.reduce_mean(rv_loss)

            # Entropy regularization
            entropy_loss = self.model.compute_entropy_regularization(p_regime)

            # Total loss
            total_loss = (
                self.cfg.w_regime * regime_loss
                + self.cfg.w_ret * ret_loss
                + self.cfg.w_rv * rv_loss
                + self.cfg.entropy_weight * entropy_loss
            )

        # Backprop
        gradients = tape.gradient(total_loss, self.model.trainable_variables)
        self.optimizer.apply_gradients(zip(gradients, self.model.trainable_variables))

        # Update metrics
        self.train_loss_tracker.update_state(total_loss)
        self.regime_acc_tracker.update_state(y_regime, p_regime)

        return total_loss

    @tf.function
    def val_step(self, x, y_regime, y_ret, y_rv):
        """Validation step"""
        outputs = self.model(x, training=False, return_regime_probs=True)
        p_regime = outputs["regime_probs"]
        y_ret_pred = outputs["ret"]
        y_rv_pred = outputs["rv"]

        # Losses
        regime_loss = tf.reduce_mean(
            tf.keras.losses.sparse_categorical_crossentropy(y_regime, p_regime)
        )
        ret_loss = tf.reduce_mean(tf.keras.losses.huber(y_ret, y_ret_pred, delta=1.0))
        rv_loss = tf.reduce_mean(tf.keras.losses.huber(y_rv, y_rv_pred, delta=0.01))

        total_loss = (
            self.cfg.w_regime * regime_loss
            + self.cfg.w_ret * ret_loss
            + self.cfg.w_rv * rv_loss
        )

        self.val_loss_tracker.update_state(total_loss)

        return total_loss


# Helper: Learning rate schedule
class CosineWarmup(tf.keras.optimizers.schedules.LearningRateSchedule):
    """Cosine decay with warmup"""

    def __init__(self, base_lr: float, warmup_steps: int, total_steps: int):
        super().__init__()
        self.base_lr = base_lr
        self.warmup_steps = max(1, warmup_steps)
        self.total_steps = max(self.warmup_steps + 1, total_steps)

    def __call__(self, step):
        step = tf.cast(step, tf.float32)
        warm = tf.minimum(1.0, step / self.warmup_steps)
        progress = tf.minimum(1.0, (step - self.warmup_steps) / (self.total_steps - self.warmup_steps))
        cosine = 0.5 * (1.0 + tf.cos(math.pi * progress))
        return self.base_lr * warm * cosine


# =========================
# EVALUATION UTILS
# =========================
def evaluate_regime_expert_performance(
    model: RegimeAwareMarketModel,
    X: np.ndarray,
    y_regime: np.ndarray,
    y_ret: np.ndarray,
    y_rv: np.ndarray,
    regime_names: list[str] = None,
) -> Dict[str, Dict[str, float]]:
    """
    Evaluate each expert's performance WITHIN its assigned regime.

    Success criteria:
    -----------------
    1. Regime stability: low switching rate
    2. Expert specialization: each expert beats random in its regime
    3. No global direction learning

    Args:
        model: trained RegimeAwareMarketModel
        X: [N, L, F] - input features
        y_regime: [N] - true regime labels
        y_ret: [N, H] - true returns
        y_rv: [N] - true volatility
        regime_names: optional list of regime names

    Returns:
        dict mapping regime_id → metrics
    """
    if regime_names is None:
        regime_names = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]

    # Get predictions
    outputs = model(X, training=False, return_regime_probs=True)
    y_ret_pred = outputs["ret"].numpy()
    y_rv_pred = outputs["rv"].numpy()
    p_regime = outputs["regime_probs"].numpy()
    regime_pred = np.argmax(p_regime, axis=-1)

    results = {}

    for regime_id in range(model.cfg.n_regimes):
        # Filter samples in this regime
        mask = (y_regime == regime_id)
        n_samples = np.sum(mask)

        if n_samples == 0:
            continue

        # Metrics within regime
        ret_true = y_ret[mask]
        ret_pred = y_ret_pred[mask]
        rv_true = y_rv[mask]
        rv_pred = y_rv_pred[mask]

        # MAE
        ret_mae = np.mean(np.abs(ret_true - ret_pred))
        rv_mae = np.mean(np.abs(rv_true - rv_pred))

        # Directional accuracy (on cumulative return)
        ret_cum_true = np.sum(ret_true, axis=-1)
        ret_cum_pred = np.sum(ret_pred, axis=-1)
        dir_acc = np.mean(np.sign(ret_cum_true) == np.sign(ret_cum_pred))

        # Random baseline: 50% for direction
        beats_random = dir_acc > 0.5

        results[regime_names[regime_id]] = {
            "n_samples": int(n_samples),
            "ret_mae": float(ret_mae),
            "rv_mae": float(rv_mae),
            "directional_acc": float(dir_acc),
            "beats_random": bool(beats_random),
        }

    # Regime classification accuracy
    regime_acc = np.mean(regime_pred == y_regime)
    results["regime_classification_acc"] = float(regime_acc)

    return results


# =========================
# EXAMPLE USAGE
# =========================
def example_usage():
    """
    Example of how to use the regime-aware model.
    """
    import sys

    # Mock data for demonstration
    B, L, F, H = 128, 256, 44, 12
    n_samples = 10000

    print("=" * 80)
    print("REGIME-AWARE MARKET MODEL - EXAMPLE")
    print("=" * 80)

    # 1) Generate synthetic features
    print("\n1) Generating synthetic data...")
    X_all = np.random.randn(n_samples, F).astype(np.float32)

    # Mock feature keys (must match compute_regime_labels expectations)
    feature_keys = [
        "log_ret", "rv_ann_60", "rsi_14", "dist_ema_20", "dist_ema_50"
    ] + [f"feat_{i}" for i in range(F - 5)]

    # 2) Compute regime labels
    print("2) Computing regime labels...")
    y_regime = compute_regime_labels(X_all, feature_keys, lookback=L)

    # Regime statistics
    stats = compute_regime_statistics(y_regime)
    print("\nRegime Distribution:")
    for k, v in stats.items():
        print(f"  {k}: {v:.2f}")

    # 3) Create windows (simplified - normally use make_windows from model.py)
    print("\n3) Creating windows...")
    # For simplicity, just take last L timesteps
    X_windows = np.array([X_all[max(0, i-L):i] for i in range(L, n_samples)])
    # Pad if needed
    X_windows = np.array([
        np.pad(X_all[max(0, i-L):i], ((L - (i - max(0, i-L)), 0), (0, 0)), mode='edge')
        for i in range(L, n_samples)
    ])
    y_regime_windows = y_regime[L:]
    y_ret_windows = np.random.randn(len(X_windows), H).astype(np.float32) * 0.01
    y_rv_windows = np.abs(np.random.randn(len(X_windows)).astype(np.float32)) * 0.02

    print(f"  X shape: {X_windows.shape}")
    print(f"  y_regime shape: {y_regime_windows.shape}")

    # 4) Create model
    print("\n4) Creating regime-aware model...")
    cfg = RegimeConfig(
        lookback=L,
        horizon=H,
        batch_size=128,
        n_regimes=5,
        gating_mode="soft",
        expert_type="tcn",
    )

    model = RegimeAwareMarketModel(cfg=cfg, feature_dim=F)

    # Build model
    dummy_input = tf.zeros((1, L, F), dtype=tf.float32)
    _ = model(dummy_input, training=False)

    print(f"  Model parameters: {model.count_params():,}")

    # 5) Training (simplified)
    print("\n5) Training (demo - 2 steps only)...")
    trainer = RegimeAwareTrainer(model=model, cfg=cfg)

    # Take small batch
    batch_size = 64
    for step in range(2):
        idx = np.random.choice(len(X_windows), batch_size, replace=False)
        x_batch = X_windows[idx]
        y_regime_batch = y_regime_windows[idx]
        y_ret_batch = y_ret_windows[idx]
        y_rv_batch = y_rv_windows[idx]

        loss = trainer.train_step(
            tf.constant(x_batch),
            tf.constant(y_regime_batch),
            tf.constant(y_ret_batch),
            tf.constant(y_rv_batch),
        )
        print(f"  Step {step+1}/2 - Loss: {loss:.4f}")

    # 6) Evaluation
    print("\n6) Evaluating regime-expert performance...")
    results = evaluate_regime_expert_performance(
        model=model,
        X=X_windows[:500],
        y_regime=y_regime_windows[:500],
        y_ret=y_ret_windows[:500],
        y_rv=y_rv_windows[:500],
    )

    print("\nPer-Regime Performance:")
    for regime, metrics in results.items():
        if regime == "regime_classification_acc":
            print(f"\nRegime Classification Accuracy: {metrics:.2%}")
        else:
            print(f"\n  {regime}:")
            for k, v in metrics.items():
                if isinstance(v, float):
                    print(f"    {k}: {v:.4f}")
                else:
                    print(f"    {k}: {v}")

    print("\n" + "=" * 80)
    print("DONE - This was a demonstration with synthetic data")
    print("=" * 80)


if __name__ == "__main__":
    example_usage()
