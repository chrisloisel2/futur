"""
UNIFIED CONFIGURATION
All hyperparameters and settings in one place.
"""

from dataclasses import dataclass
from typing import Literal, List
import os


@dataclass(frozen=True)
class UnifiedConfig:
    """Single source of truth for all configuration"""

    # ===== DATA =====
    lookback: int = 256
    horizon: int = 12
    stride: int = 1
    batch_size: int = 256
    shuffle_buffer: int = 50_000
    prefetch: int = 2

    # ===== S3 =====
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_prefix: str = os.getenv("S3_PREFIX", "")
    aws_profile: str = os.getenv("AWS_PROFILE", "") or None
    aws_region: str = os.getenv("AWS_REGION", "") or None

    # ===== FEATURES =====
    feature_keys: tuple = (
        # OHLCV
        "Open", "High", "Low", "Close", "Volume",
        "Quote_Volume", "Trades", "Taker_Buy_Base", "Taker_Buy_Quote",
        # Returns / Risk
        "ret", "log_ret",
        "rv_5", "rv_15", "rv_30", "rv_60", "rv_120", "rv_240", "rv_720", "rv_1440",
        "rv_ann_5", "rv_ann_15", "rv_ann_30", "rv_ann_60", "rv_ann_120", "rv_ann_240", "rv_ann_720", "rv_ann_1440",
        # Trend
        "ema_20", "ema_50", "ema_100", "ema_200",
        "dist_ema_20", "dist_ema_50", "dist_ema_100", "dist_ema_200",
        # Vol/Oscillator
        "atr_14", "atr_pct_14", "rsi_14",
        # Tail Risk
        "var_99_60", "cvar_99_60", "var_99_240", "cvar_99_240", "var_99_1440", "cvar_99_1440",
    )

    target_ret_key: str = "log_ret"
    target_rv_key: str = "rv_60"

    # ===== REGIME CLASSIFIER =====
    n_regimes: int = 5
    regime_backbone: Literal["cnn", "tcn"] = "cnn"
    regime_d_model: int = 64
    regime_n_layers: int = 3
    regime_dropout: float = 0.15

    # ===== EXPERTS =====
    expert_type: Literal["tcn", "transformer"] = "tcn"
    expert_d_model: int = 64
    expert_n_layers: int = 2
    expert_dropout: float = 0.20

    # ===== GATING =====
    gating_mode: Literal["hard", "soft"] = "soft"
    entropy_weight: float = 0.01

    # ===== TRAINING =====
    lr: float = 3e-4
    weight_decay: float = 1e-4
    clip_norm: float = 1.0
    epochs: int = 20
    pretrain_regime_epochs: int = 5
    steps_per_epoch: int = 2000
    val_steps: int = 200

    # ===== LOSS WEIGHTS =====
    w_regime: float = 0.3
    w_ret: float = 1.0
    w_rv: float = 0.4
    w_dir: float = 0.0  # Direction NOT learned globally

    # ===== EVALUATION =====
    eval_direction_threshold: float = 0.25  # Fraction of std
    eval_min_threshold: float = 0.0005     # Minimum absolute
    eval_significance_level: float = 0.05
    eval_enable_leakage_test: bool = True
    eval_enable_variance_shift: bool = True

    # ===== OUTPUT =====
    output_dir: str = "output"
    save_scaler: bool = True
    save_splits: bool = True

    # ===== MISC =====
    seed: int = 1337
    mixed_precision: bool = True
    xla: bool = True


# Global config instance
CONFIG = UnifiedConfig()


# Regime names (constants)
REGIME_NAMES = ["TREND", "MEAN_REVERT", "HIGH_VOL", "LOW_VOL", "RANGE"]
