"""
Script d'entraînement principal pour le modèle de trading multimodal.
"""
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import torch

from models.multi_modal_trading import MultiModalTradingModel
from data.pipeline import DataPipeline, EnhancedDataPipeline
from training.trainer import TradingTrainer
from utils.config import load_config
from utils.metrics import ModelMetrics


def _select_device(requested: str) -> str:
    """Resolve device, preferring MPS on Mac if available."""
    if requested != "auto":
        return requested
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _apply_mps_optimizations(device: str, training_cfg: Dict[str, Any], model_cfg: Dict[str, Any]) -> None:
    """Tune training config for MPS and large models."""
    if device == "mps" and hasattr(torch, "mps"):
        try:
            torch.mps.set_per_process_memory_fraction(0.8)
            torch.mps.empty_cache()
        except Exception:
            logging.warning("MPS optimization failed; continuing without per-process tuning.")

        # Cap batch size for MPS to avoid OOM
        if "batch_size" in training_cfg:
            training_cfg["batch_size"] = min(32, training_cfg["batch_size"])

    # Gradient accumulation for large hidden sizes
    d_model = model_cfg.get("params", {}).get("d_model", 0)
    if d_model > 512:
        training_cfg.setdefault("gradient_accumulation_steps", 4)
        training_cfg["gradient_accumulation_steps"] = max(4, training_cfg["gradient_accumulation_steps"])


def setup_training(
    config_path: str,
    device: str = "auto",
    debug_mode: bool = False,
    fast_dev_run: bool = False,
    use_alternative_data: bool = False,
) -> Any:
    """
    Configure and launch training.

    Args:
        config_path: Path to YAML configuration
        device: 'mps', 'cpu', or 'auto'
        debug_mode: Enable lighter run for debugging
        fast_dev_run: Single-epoch quick sanity check
    """
    config = load_config(config_path)

    device = _select_device(device)
    logging.info("Using device: %s", device)

    training_cfg = config.get("training", {})
    model_cfg = config.get("model", {})

    if debug_mode:
        training_cfg["epochs"] = 1
        training_cfg["debug_mode"] = True
        training_cfg.setdefault("limit_batches", 10)

    if fast_dev_run:
        training_cfg["fast_dev_run"] = True
        training_cfg["epochs"] = 1

    _apply_mps_optimizations(device, training_cfg, model_cfg)

    # Data pipeline
    data_cfg = config.get("data", {})
    data_cfg.setdefault("sources", [])
    if use_alternative_data:
        data_cfg["sources"].extend(
            [
                "twitter_sentiment",
                "reddit_momentum",
                "whale_alert_stream",
            ]
        )

    pipeline_cls = EnhancedDataPipeline if use_alternative_data else DataPipeline
    if use_alternative_data:
        data_pipeline = pipeline_cls(data_cfg, enable_alternative=True)
    else:
        data_pipeline = pipeline_cls(data_cfg)
    train_loader, val_loader, test_loader = data_pipeline.get_data_loaders()

    # Sync feature dimension with the model config
    model_cfg.setdefault("params", {})
    model_cfg["params"]["feature_dim"] = getattr(data_pipeline, "feature_dim", model_cfg["params"].get("feature_dim", 128))

    # Model
    model = MultiModalTradingModel(model_cfg)
    model.to(device)

    # Trainer
    trainer = TradingTrainer(
        model=model,
        config=training_cfg,
        device=device,
        metrics_callback=ModelMetrics.track_all,
    )

    # Train
    trainer.fit(train_loader, val_loader)

    # Evaluate
    test_metrics = trainer.evaluate(test_loader)
    logging.info("Test metrics: %s", test_metrics)

    # Save
    ckpt_dir = Path(training_cfg.get("checkpoint_dir", "checkpoints"))
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = ckpt_dir / f"model_{datetime.now().strftime('%Y%m%d_%H%M')}.pt"
    trainer.save_checkpoint(ckpt_path)
    logging.info("Checkpoint saved to %s", ckpt_path)

    return test_metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, required=True, help="Chemin vers le fichier de config")
    parser.add_argument("--device", type=str, default="auto", help="Device (mps, cpu, auto)")
    parser.add_argument("--log_level", type=str, default="INFO", help="Niveau de log")
    parser.add_argument("--debug_mode", action="store_true", help="Active un run léger de debug")
    parser.add_argument("--fast_dev_run", action="store_true", help="Active un sanity check rapide (1 epoch)")
    parser.add_argument("--use_alternative_data", action="store_true", help="Active les sources alternatives (stub)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    setup_training(
        args.config,
        args.device,
        args.debug_mode,
        args.fast_dev_run,
        args.use_alternative_data,
    )
