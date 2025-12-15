"""
Tiny Recursive Model (TRM) for algorithmic trading.

Based on the paradigm "Less is More: Recursive Reasoning with Tiny Networks".

Key principles:
- Minimal parameters (~10-50K)
- Recursive reasoning with shared weights
- Trading-aware loss functions
- Rigorous temporal validation
- Robustness testing

Modules:
- data: Feature engineering, data loading, S3 integration
- model: TRM architecture, loss functions
- training: Training loop, optimization
- evaluation: Trading metrics, backtesting
- robustness: Generalization tests
"""

__version__ = "1.0.0"

# Import key components for easy access
from .data import S3TRMDataLoader, build_trm_features, create_dataloaders
from .evaluation import TRMBacktester, compute_all_metrics, print_metrics_report
from .model import CompositeTradingLoss, TinyRecursiveModel, TRMEnsemble
from .robustness import run_all_robustness_tests
from .training import TRMTrainer

__all__ = [
    # Data
    'S3TRMDataLoader',
    'build_trm_features',
    'create_dataloaders',
    # Model
    'TinyRecursiveModel',
    'TRMEnsemble',
    'CompositeTradingLoss',
    # Training
    'TRMTrainer',
    # Evaluation
    'TRMBacktester',
    'compute_all_metrics',
    'print_metrics_report',
    # Robustness
    'run_all_robustness_tests',
]
