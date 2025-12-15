#!/usr/bin/env python3
"""
Main training script for TRM.

Usage:
    python train_trm.py --config config.yaml
    python train_trm.py --symbol BTCUSDT --epochs 50
"""
import argparse
import logging
import sys
from pathlib import Path

import torch
import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trm.data import S3TRMDataLoader
from trm.evaluation import TRMBacktester, print_metrics_report
from trm.model import CompositeTradingLoss, TinyRecursiveModel
from trm.robustness import run_all_robustness_tests
from trm.training import TRMTrainer

logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def setup_logging(config: dict):
    """Setup logging configuration."""
    log_level = getattr(logging, config['logging']['level'])

    handlers = []

    if config['logging']['log_to_console']:
        handlers.append(logging.StreamHandler())

    if config['logging']['log_file']:
        log_file = Path(config['logging']['log_file'])
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def set_seed(seed: int):
    """Set random seed for reproducibility."""
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def get_device(config: dict) -> str:
    """Determine device to use."""
    device_config = config['hardware']['device']

    if device_config == 'auto':
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    else:
        return device_config


def main(config: dict):
    """Main training pipeline."""

    # Setup
    setup_logging(config)
    set_seed(config['seed'])
    device = get_device(config)

    logger.info("=" * 80)
    logger.info("TINY RECURSIVE MODEL (TRM) TRAINING")
    logger.info("=" * 80)
    logger.info(f"Device: {device}")
    logger.info(f"Seed: {config['seed']}")

    # =========================================================================
    # STEP 1: Load Data
    # =========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1: LOADING DATA")
    logger.info("=" * 80)

    data_loader = S3TRMDataLoader(
        s3_bucket=config['data']['s3_bucket'],
        s3_prefix=config['data']['s3_prefix'],
        symbols=config['data']['symbols'],
        start_year=config['data']['start_year'],
        end_year=config['data']['end_year'],
        lookback_window=config['data']['lookback_window'],
        batch_size=config['training']['batch_size'],
        cache_dir=config['data']['cache_dir']
    )

    train_loader, val_loader, test_loader, metadata = data_loader.prepare_dataloaders(
        symbol=config['data']['symbol_filter'],
        normalize=config['data']['normalize'],
        prediction_horizon=config['data']['prediction_horizon']
    )

    logger.info(f"Data loaded successfully:")
    logger.info(f"  Train samples: {metadata['train_samples']}")
    logger.info(f"  Val samples:   {metadata['val_samples']}")
    logger.info(f"  Test samples:  {metadata['test_samples']}")
    logger.info(f"  Features:      {metadata['num_features']}")
    logger.info(f"  Lookback:      {metadata['lookback_window']}")

    # =========================================================================
    # STEP 2: Create Model
    # =========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: CREATING MODEL")
    logger.info("=" * 80)

    model = TinyRecursiveModel(
        num_features=metadata['num_features'],
        latent_dim=config['model']['latent_dim'],
        hidden_dim=config['model']['hidden_dim'],
        num_iterations=config['model']['num_iterations'],
        dropout=config['model']['dropout'],
        output_mode=config['model']['output_mode']
    )

    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    logger.info(f"Model created:")
    logger.info(f"  Total parameters:      {total_params:,}")
    logger.info(f"  Trainable parameters:  {trainable_params:,}")
    logger.info(f"  Latent dimension:      {config['model']['latent_dim']}")
    logger.info(f"  Recursive iterations:  {config['model']['num_iterations']}")

    # =========================================================================
    # STEP 3: Create Loss Function
    # =========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: CREATING LOSS FUNCTION")
    logger.info("=" * 80)

    loss_fn = CompositeTradingLoss(
        alpha=config['loss']['alpha'],
        beta=config['loss']['beta'],
        gamma=config['loss']['gamma'],
        delta=config['loss']['delta'],
        trading_fee=config['loss']['trading_fee'],
        max_acceptable_drawdown=config['loss']['max_acceptable_drawdown'],
        magnitude_temperature=config['loss']['magnitude_temperature']
    )

    logger.info("Trading-aware composite loss created")
    logger.info(f"  α (directional):    {config['loss']['alpha']}")
    logger.info(f"  β (magnitude):      {config['loss']['beta']}")
    logger.info(f"  γ (trading cost):   {config['loss']['gamma']}")
    logger.info(f"  δ (drawdown):       {config['loss']['delta']}")

    # =========================================================================
    # STEP 4: Train Model
    # =========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: TRAINING MODEL")
    logger.info("=" * 80)

    trainer = TRMTrainer(
        model=model,
        loss_fn=loss_fn,
        train_loader=train_loader,
        val_loader=val_loader,
        learning_rate=config['training']['learning_rate'],
        weight_decay=config['training']['weight_decay'],
        max_epochs=config['training']['max_epochs'],
        patience=config['training']['patience'],
        grad_clip_norm=config['training']['grad_clip_norm'],
        device=device,
        checkpoint_dir=config['training']['checkpoint_dir'],
        use_amp=config['training']['use_amp'] and device == 'cuda'
    )

    training_history = trainer.train()

    logger.info("\nTraining completed:")
    logger.info(f"  Final epoch:           {training_history['final_epoch']}")
    logger.info(f"  Best validation Sharpe: {training_history['best_val_sharpe']:.4f}")
    logger.info(f"  Best validation loss:   {training_history['best_val_loss']:.6f}")

    # =========================================================================
    # STEP 5: Evaluate on Test Set
    # =========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("STEP 5: EVALUATING ON TEST SET")
    logger.info("=" * 80)

    # Load best model
    best_checkpoint = Path(config['training']['checkpoint_dir']) / 'checkpoint_best.pt'
    if best_checkpoint.exists():
        logger.info(f"Loading best checkpoint: {best_checkpoint}")
        checkpoint = torch.load(best_checkpoint, map_location=device)
        model.load_state_dict(checkpoint['model_state_dict'])

    backtester = TRMBacktester(
        model=model,
        test_loader=test_loader,
        trading_fee=config['loss']['trading_fee'],
        initial_capital=config['evaluation']['initial_capital'],
        device=device
    )

    test_metrics = backtester.run_backtest(verbose=True)

    # =========================================================================
    # STEP 6: Robustness Tests
    # =========================================================================
    if any([
        config['robustness']['test_timeframes'],
        config['robustness']['test_noise'],
        config['robustness']['test_crisis']
    ]):
        logger.info("\n" + "=" * 80)
        logger.info("STEP 6: ROBUSTNESS TESTS")
        logger.info("=" * 80)

        # Get test data as tensors
        test_features_list = []
        test_targets_list = []

        for X, y in test_loader:
            test_features_list.append(X)
            test_targets_list.append(y)

        test_features = torch.cat(test_features_list)
        test_targets = torch.cat(test_targets_list)

        robustness_results = run_all_robustness_tests(
            model=model,
            test_features=test_features,
            test_targets=test_targets,
            device=device
        )

    # =========================================================================
    # SUMMARY
    # =========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("TRAINING SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Model:                 TinyRecursiveModel ({trainable_params:,} params)")
    logger.info(f"Training epochs:       {training_history['final_epoch']}")
    logger.info(f"Best val Sharpe:       {training_history['best_val_sharpe']:.4f}")
    logger.info(f"Test Sharpe ratio:     {test_metrics['sharpe_ratio']:.4f}")
    logger.info(f"Test return:           {test_metrics['total_return_pct']:.2f}%")
    logger.info(f"Test max drawdown:     {test_metrics['max_drawdown_pct']:.2f}%")
    logger.info(f"Test win rate:         {test_metrics['win_rate_pct']:.2f}%")
    logger.info(f"Test profit factor:    {test_metrics['profit_factor']:.4f}")
    logger.info("=" * 80)

    logger.info("\nTraining complete! Check outputs:")
    logger.info(f"  Checkpoints: {config['training']['checkpoint_dir']}")
    logger.info(f"  Logs:        {config['logging']['log_file']}")

    return {
        'model': model,
        'training_history': training_history,
        'test_metrics': test_metrics,
        'metadata': metadata
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Tiny Recursive Model for trading")
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file'
    )
    parser.add_argument('--symbol', type=str, help='Symbol to train on (overrides config)')
    parser.add_argument('--epochs', type=int, help='Max epochs (overrides config)')
    parser.add_argument('--device', type=str, choices=['cuda', 'cpu'], help='Device (overrides config)')

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Override with CLI arguments
    if args.symbol:
        config['data']['symbol_filter'] = args.symbol
    if args.epochs:
        config['training']['max_epochs'] = args.epochs
    if args.device:
        config['hardware']['device'] = args.device

    # Run training
    try:
        results = main(config)
        print("\n✓ Training completed successfully!")
    except Exception as e:
        logger.error(f"Training failed: {e}", exc_info=True)
        print(f"\n✗ Training failed: {e}")
        sys.exit(1)
