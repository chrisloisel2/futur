#!/usr/bin/env python3
"""
Script d'Entraînement Avancé pour TinyRecursiveMarketModel
Charge données année par année depuis S3, compute 30+ KPIs, TensorBoard logging
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from dataclasses import dataclass, asdict

import yaml
import numpy as np
import tensorflow as tf

# Imports locaux
from ai.models.model import (
    TinyRecursiveMarketModel,
    TRMConfig,
    make_optimizer,
    huber_loss,
    rv_loss,
    set_seed,
    FEATURE_KEYS,
)

from ai.s3_parquet_loader import S3ParquetLoader
from ai.data_pipeline import (
    fit_scaler_streaming,
    save_windows_to_disk,
)
from ai.data_pipeline_memory_efficient import (
    build_tf_dataset_from_disk_efficient,
)
from ai.training_callbacks import build_callbacks
from ai.advanced_metrics import compute_all_metrics, print_metrics_summary


def setup_logging(log_dir: str):
    """Configure logging"""
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

    return logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config


def create_trm_config_from_dict(config_dict: dict) -> TRMConfig:
    """Create TRMConfig from config dictionary"""
    model_cfg = config_dict['model']
    train_cfg = config_dict['training']
    loss_cfg = config_dict['loss_weights']

    return TRMConfig(
        # Data
        lookback=model_cfg['lookback'],
        horizon=model_cfg['horizon'],
        stride=model_cfg['stride'],
        batch_size=train_cfg['batch_size'],
        shuffle_buffer=train_cfg['shuffle_buffer'],
        prefetch=train_cfg['prefetch'],
        # Model
        d_model=model_cfg['d_model'],
        n_heads=model_cfg['n_heads'],
        d_ff=model_cfg['d_ff'],
        dropout=model_cfg['dropout'],
        mem_dim=model_cfg['mem_dim'],
        mem_update_iters=model_cfg['mem_update_iters'],
        # Training
        lr=train_cfg['lr'],
        weight_decay=train_cfg['weight_decay'],
        clip_norm=train_cfg['clip_norm'],
        epochs=train_cfg['epochs'],
        steps_per_epoch=train_cfg['steps_per_epoch'],
        val_steps=train_cfg['val_steps'],
        seed=train_cfg['seed'],
        # Loss weights
        w_ret=loss_cfg['w_ret'],
        w_dir=loss_cfg['w_dir'],
        w_rv=loss_cfg['w_rv'],
    )


def main(config_path: str):
    """Main training function"""

    # ========================================
    # 1. SETUP
    # ========================================
    print("\n" + "="*80)
    print("ADVANCED TRAINING PIPELINE FOR TinyRecursiveMarketModel")
    print("="*80 + "\n")

    # Load config
    config = load_config(config_path)
    output_dir = config['output']['base_dir']

    # Setup logging
    logger = setup_logging(os.path.join(output_dir, "logs"))
    logger.info(f"Configuration loaded from: {config_path}")
    logger.info(f"Output directory: {output_dir}")

    # Create TRMConfig
    trm_config = create_trm_config_from_dict(config)
    logger.info(f"TRM Config: {asdict(trm_config)}")

    # Set seed
    set_seed(trm_config.seed)
    logger.info(f"Random seed set to: {trm_config.seed}")

    # ========================================
    # 2. INITIALIZE S3 LOADER
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("PHASE 1/4: INITIALIZING S3 LOADER")
    logger.info("="*80)

    loader = S3ParquetLoader(
        bucket=config['data']['bucket'],
        base_prefix=config['data']['base_prefix']
    )

    years_train = config['data']['years_train']
    years_test = config['data']['years_test']

    logger.info(f"Training years: {years_train}")
    logger.info(f"Test years: {years_test}")

    # ========================================
    # 3. FIT SCALER (STREAMING)
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("PHASE 2/4: FITTING ROBUST SCALER")
    logger.info("="*80)

    scaler_path = os.path.join(output_dir, "scaler.pkl")

    if os.path.exists(scaler_path):
        logger.info(f"Scaler already exists at {scaler_path}, skipping fit...")
        from ai.data_pipeline import StreamingRobustScaler
        scaler = StreamingRobustScaler.load(scaler_path, feature_dim=len(FEATURE_KEYS))
    else:
        logger.info("Fitting scaler on training years...")
        scaler = fit_scaler_streaming(
            loader=loader,
            years=years_train,
            feature_dim=len(FEATURE_KEYS),
            verbose=True
        )

        # Save scaler
        scaler.save(scaler_path)
        logger.info(f"Scaler saved to: {scaler_path}")

    # ========================================
    # 4. CREATE WINDOWS (STREAMING + SAVE TO DISK)
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("PHASE 3/4: CREATING WINDOWS")
    logger.info("="*80)

    windows_train_dir = os.path.join(output_dir, "windows_train")
    windows_test_dir = os.path.join(output_dir, "windows_test")

    # Training windows
    if os.path.exists(windows_train_dir) and len(os.listdir(windows_train_dir)) > 0:
        logger.info(f"Training windows already exist at {windows_train_dir}, skipping...")
    else:
        logger.info("Creating training windows...")
        save_windows_to_disk(
            loader=loader,
            years=years_train,
            scaler=scaler,
            lookback=trm_config.lookback,
            horizon=trm_config.horizon,
            stride=trm_config.stride,
            output_dir=windows_train_dir,
            verbose=True
        )

    # Test windows
    if os.path.exists(windows_test_dir) and len(os.listdir(windows_test_dir)) > 0:
        logger.info(f"Test windows already exist at {windows_test_dir}, skipping...")
    else:
        logger.info("Creating test windows...")
        save_windows_to_disk(
            loader=loader,
            years=years_test,
            scaler=scaler,
            lookback=trm_config.lookback,
            horizon=trm_config.horizon,
            stride=trm_config.stride,
            output_dir=windows_test_dir,
            verbose=True
        )

    # ========================================
    # 5. BUILD TF.DATA DATASETS
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("PHASE 4/4: BUILDING TENSORFLOW DATASETS (MEMORY EFFICIENT)")
    logger.info("="*80)

    ds_train = build_tf_dataset_from_disk_efficient(
        windows_dir=windows_train_dir,
        years=years_train,
        batch_size=trm_config.batch_size,
        shuffle_buffer=trm_config.shuffle_buffer,
        training=True,
        prefetch=trm_config.prefetch,
        verbose=True
    )

    ds_val = build_tf_dataset_from_disk_efficient(
        windows_dir=windows_test_dir,
        years=years_test,
        batch_size=trm_config.batch_size,
        shuffle_buffer=1,
        training=False,
        prefetch=trm_config.prefetch,
        verbose=True
    )

    # ========================================
    # 6. BUILD MODEL
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("BUILDING MODEL")
    logger.info("="*80)

    model = TinyRecursiveMarketModel(
        cfg=trm_config,
        feature_dim=len(FEATURE_KEYS)
    )

    # Optimizer
    optimizer = make_optimizer(trm_config)

    # Losses
    losses = {
        "ret": lambda yt, yp: huber_loss(yt, yp, delta=1.0),
        "dir": tf.keras.losses.SparseCategoricalCrossentropy(),
        "rv": rv_loss,
    }
    loss_weights = {
        "ret": trm_config.w_ret,
        "dir": trm_config.w_dir,
        "rv": trm_config.w_rv
    }

    # Metrics
    metrics = {
        "ret": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
        "dir": [tf.keras.metrics.SparseCategoricalAccuracy(name="acc")],
        "rv": [tf.keras.metrics.MeanAbsoluteError(name="mae")],
    }

    # Compile
    model.compile(
        optimizer=optimizer,
        loss=losses,
        loss_weights=loss_weights,
        metrics=metrics
    )

    logger.info("Model compiled successfully")

    # Model summary
    logger.info("\nModel Summary:")
    model.build(input_shape=(None, trm_config.lookback, len(FEATURE_KEYS)))
    model.summary(print_fn=logger.info)

    # ========================================
    # 7. BUILD CALLBACKS
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("BUILDING CALLBACKS")
    logger.info("="*80)

    callbacks = build_callbacks(
        config=trm_config,
        validation_data=ds_val,
        output_base_dir=output_dir
    )

    logger.info(f"Built {len(callbacks)} callbacks")

    # ========================================
    # 8. TRAIN MODEL
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("STARTING TRAINING")
    logger.info("="*80)
    logger.info(f"Epochs: {trm_config.epochs}")
    logger.info(f"Steps per epoch: {trm_config.steps_per_epoch}")
    logger.info(f"Validation steps: {trm_config.val_steps}")

    start_time = datetime.now()
    logger.info(f"Training started at: {start_time}")

    try:
        history = model.fit(
            ds_train,
            validation_data=ds_val,
            epochs=trm_config.epochs,
            steps_per_epoch=trm_config.steps_per_epoch,
            validation_steps=trm_config.val_steps,
            callbacks=callbacks,
            verbose=1
        )

        end_time = datetime.now()
        duration = end_time - start_time
        logger.info(f"\nTraining completed at: {end_time}")
        logger.info(f"Total duration: {duration}")

    except KeyboardInterrupt:
        logger.warning("\nTraining interrupted by user!")
        end_time = datetime.now()
        duration = end_time - start_time
        logger.info(f"Training duration before interruption: {duration}")

    # ========================================
    # 9. FINAL EVALUATION
    # ========================================
    logger.info("\n" + "="*80)
    logger.info("FINAL EVALUATION ON TEST SET")
    logger.info("="*80)

    # Collect all predictions
    y_true_dict = {'ret': [], 'dir': [], 'rv': []}
    y_pred_dict = {'ret': [], 'dir': [], 'rv': []}

    logger.info("Collecting predictions on full test set...")
    for X_batch, y_batch in ds_val:
        y_pred = model(X_batch, training=False)

        y_true_dict['ret'].append(y_batch['ret'].numpy())
        y_true_dict['dir'].append(y_batch['dir'].numpy())
        y_true_dict['rv'].append(y_batch['rv'].numpy())

        y_pred_dict['ret'].append(y_pred['ret'].numpy())
        y_pred_dict['dir'].append(y_pred['dir'].numpy())
        y_pred_dict['rv'].append(y_pred['rv'].numpy())

    # Concatenate
    y_true_dict = {k: np.concatenate(v, axis=0) for k, v in y_true_dict.items()}
    y_pred_dict = {k: np.concatenate(v, axis=0) for k, v in y_pred_dict.items()}

    logger.info(f"Collected predictions for {len(y_true_dict['ret'])} samples")

    # Compute all metrics
    final_metrics = compute_all_metrics(y_true_dict, y_pred_dict, periods_per_year=525600)

    # Print summary
    print_metrics_summary(final_metrics, "FINAL TEST SET METRICS")

    # Save to JSON
    metrics_file = os.path.join(output_dir, "metrics", "final_metrics.json")
    with open(metrics_file, 'w') as f:
        # Convert numpy types to native Python types for JSON serialization
        serializable_metrics = {}
        for k, v in final_metrics.items():
            if isinstance(v, (list, tuple)):
                serializable_metrics[k] = [float(x) if isinstance(x, np.floating) else x for x in v]
            elif isinstance(v, (np.integer, np.floating)):
                serializable_metrics[k] = float(v)
            else:
                serializable_metrics[k] = v

        json.dump(serializable_metrics, f, indent=2)

    logger.info(f"Final metrics saved to: {metrics_file}")

    # ========================================
    # 10. SAVE FINAL MODEL
    # ========================================
    final_model_path = os.path.join(output_dir, "checkpoints", "final_model.keras")
    model.save(final_model_path)
    logger.info(f"Final model saved to: {final_model_path}")

    # ========================================
    # TRAINING COMPLETE
    # ========================================
    print("\n" + "="*80)
    print("TRAINING COMPLETE!")
    print("="*80)
    print(f"\nResults:")
    print(f"  Best model:     {output_dir}/checkpoints/best_val_loss.keras")
    print(f"  Final model:    {final_model_path}")
    print(f"  Metrics:        {output_dir}/metrics/")
    print(f"  Logs:           {output_dir}/logs/")
    print(f"\nTo view TensorBoard:")
    print(f"  tensorboard --logdir={output_dir}/tensorboard/ --port=6006")
    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train TinyRecursiveMarketModel with advanced metrics")
    parser.add_argument(
        "--config",
        type=str,
        default="ai/configs/train_advanced.yaml",
        help="Path to configuration YAML file"
    )

    args = parser.parse_args()

    # Check if config exists
    if not os.path.exists(args.config):
        print(f"Error: Configuration file not found: {args.config}")
        print(f"Please create it first or use --config to specify another path")
        sys.exit(1)

    # Run training
    main(args.config)
