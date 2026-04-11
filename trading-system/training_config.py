"""
UNIFIED TRAINING CONFIGURATION
Production-grade hyperparameters for all pipeline components
"""
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class MarketConfig:
    """Real-world market environment parameters"""
    # Trading costs
    fee_bps: float = 4.0                    # Binance maker/taker 0.04%
    slippage_bps: float = 2.0               # Average slippage

    # Position management
    leverage: float = 1.0                   # No leverage (conservative)
    max_position_size: float = 1.0          # 100% of equity
    min_trade_size_usd: float = 10.0        # Minimum order size

    # Risk management
    max_drawdown_stop: float = 0.15         # Stop trading at 15% DD
    max_daily_trades: int = 20              # Prevent overtrading
    max_correlation: float = 0.7            # Max correlation between active trades

    # Execution
    order_timeout_sec: int = 30             # Cancel if not filled
    partial_fill_threshold: float = 0.9     # Accept if >90% filled


@dataclass
class DataConfig:
    """Data loading and preprocessing"""
    symbol: str = "BTCUSDT"
    timeframe: str = "1m"
    start_date: str = "2023-01-01"
    end_date: str = "2025-12-31"

    # Temporal validation splits
    train_pct: float = 0.70
    val_pct: float = 0.15
    test_pct: float = 0.15

    # Walk-forward validation
    n_folds: int = 5                        # Number of temporal folds
    min_train_days: int = 180               # Minimum training period

    # Feature engineering
    use_alternative_data: bool = False      # News, sentiment, etc.
    use_regime_conditioning: bool = True    # Regime-aware features


@dataclass
class EdgeForecasterConfig:
    """Edge forecaster (Transformer) hyperparameters"""
    # Architecture
    seq_len: int = 64                       # Sequence length (bars) — 64 bars de contexte pour un horizon 60 min
    d_model: int = 192                      # Model dimension
    n_heads: int = 6                        # Attention heads
    n_layers: int = 5                       # Transformer layers
    d_ff: int = 512                         # Feedforward dimension
    dropout: float = 0.05                   # Dropout rate
    attn_dropout: float = 0.02              # Attention dropout

    # Multi-task outputs
    quantiles: List[float] = field(default_factory=lambda: [0.05, 0.25, 0.50, 0.75, 0.95])
    horizon_minutes: int = 60               # Horizon unique : 60 min (aligné avec Level 0 et aggregate_features)

    # Labels (TP/SL)
    # Pour 1m crypto data avec horizon 60min
    # Median ATR ~0.05%, tp_k=3.0 → ~0.15% TP threshold sur 60 min
    tp_k: float = 3.0                       # TP = tp_k * ATR
    sl_k: float = 2.0                       # SL = sl_k * ATR
    adaptive_tp: bool = False               # Regime-adaptive TP

    # Training
    epochs: int = 40
    batch_size: int = 256
    lr: float = 1e-3                        # Increased from 3e-4 to 1e-3 (3.3x higher)
    weight_decay: float = 1e-5
    warmup_pct: float = 0.10                # LR warmup
    label_smoothing: float = 0.02           # Prevent overconfidence
    grad_clip: float = 5.0                  # Gradient clipping

    # Optimization
    optimizer: str = "adamw"
    scheduler: str = "cosine"
    ema_decay: float = 0.999                # EMA weights
    amp: bool = True                        # Mixed precision
    compile: bool = False                   # torch.compile (slower first run)

    # Early stopping
    patience: int = 15
    min_delta: float = 5e-5

    # Calibration
    temperature_scaling: bool = True        # Platt scaling for probabilities
    bootstrap_samples: int = 100            # CI stability check

    # Device
    device: str = "cpu"
    num_workers: int = 0


@dataclass
class RegimeClassifierConfig:
    """Regime classifier hyperparameters"""
    # Classes
    regimes: List[str] = field(default_factory=lambda: [
        "impulse", "reversal", "breakout", "squeeze", "calm", "chop"
    ])

    # Model
    model_type: str = "logistic"            # logistic, xgboost, lightgbm
    class_weight: str = "balanced"          # Handle class imbalance
    C: float = 2.0                          # Regularization (lower = more)
    max_iter: int = 500

    # XGBoost/LightGBM (if used)
    n_estimators: int = 100
    max_depth: int = 6
    learning_rate: float = 0.1


@dataclass
class SpecialistConfig:
    """Regime-specific specialist models"""
    enabled: bool = False                   # Currently not used

    # Per-regime models
    impulse_model: str = "linear"
    reversal_model: str = "linear"
    breakout_model: str = "linear"

    # Training
    min_samples_per_regime: int = 1000      # Minimum data required


@dataclass
class GatingConfig:
    """Gating network (meta-learner) configuration"""
    enabled: bool = False                   # Currently not used

    # Architecture
    hidden_dims: List[int] = field(default_factory=lambda: [128, 64])
    dropout: float = 0.1

    # Training
    epochs: int = 20
    lr: float = 1e-3


@dataclass
class ProxyMetricsConfig:
    """Trading proxy for model selection during training"""
    # Threshold selection
    threshold_percentile: float = 85.0      # Only trade top 15% signals
    threshold_min: float = 0.50             # Floor at 50% confidence

    # Frequency cap
    max_trades_per_day: int = 20            # Prevent overtrading
    val_days: int = 30                      # Validation period length

    # Bootstrap stability
    bootstrap_samples: int = 100            # For Sharpe CI
    ci_width_threshold: float = 2.0         # Reject if CI too wide

    # Scoring
    min_trades: int = 50                    # Minimum viable sample
    trade_count_penalty: float = 10.0       # Penalty per missing trade


@dataclass
class ValidationConfig:
    """Validation and testing configuration"""
    # No-lookahead proof
    lookahead_tests: int = 50               # Number of shuffle tests
    lookahead_horizon: int = 60             # Shuffle window

    # Calibration metrics
    ece_bins: int = 10                      # Expected calibration error
    brier_threshold: float = 0.25           # Max acceptable Brier score

    # Temporal stability
    check_rolling_stability: bool = True    # Detect distribution shift
    stability_window: int = 1000            # Rolling window size
    max_feature_drift: float = 3.0          # Max z-score drift

    # Quality gates
    min_val_sharpe: float = 0.5             # Minimum Sharpe to deploy
    max_val_drawdown: float = 0.20          # Maximum drawdown
    min_win_rate: float = 0.45              # Minimum win rate


@dataclass
class UnifiedTrainingConfig:
    """Master configuration for all components"""
    # Sub-configs
    market: MarketConfig = field(default_factory=MarketConfig)
    data: DataConfig = field(default_factory=DataConfig)
    edge: EdgeForecasterConfig = field(default_factory=EdgeForecasterConfig)
    regime: RegimeClassifierConfig = field(default_factory=RegimeClassifierConfig)
    specialist: SpecialistConfig = field(default_factory=SpecialistConfig)
    gating: GatingConfig = field(default_factory=GatingConfig)
    proxy: ProxyMetricsConfig = field(default_factory=ProxyMetricsConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)

    # Global settings
    seed: int = 1337
    deterministic: bool = False             # Slower but reproducible
    log_level: str = "INFO"
    log_interval: int = 300                 # Log every N batches

    # Output paths
    output_dir: str = "artifacts/models"
    run_id: str = "production_v1"
    save_checkpoints: bool = True
    save_best_trading: bool = True          # Save best proxy score
    save_best_val_loss: bool = True         # Save best validation loss

    # Components to train
    train_regime: bool = True
    train_edge: bool = True

    # Walk-forward
    use_walk_forward: bool = False          # Multi-fold temporal validation

    def __post_init__(self):
        # Validate percentages sum to 1.0
        total = self.data.train_pct + self.data.val_pct + self.data.test_pct
        assert abs(total - 1.0) < 1e-6, f"train+val+test must sum to 1.0, got {total}"

        # Ensure device consistency
        self.edge.device = self.edge.device or "cpu"
