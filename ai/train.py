"""
Script d'entraînement principal pour le modèle de trading multimodal.
"""
import argparse
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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


def _apply_mps_optimizations(
    device: str,
    training_cfg: Dict[str, Any],
    model_cfg: Dict[str, Any],
    memory_fraction: Optional[float] = None,
) -> None:
    """Tune training config for MPS and large models."""
    if device == "mps" and hasattr(torch, "mps"):
        target_fraction = memory_fraction if memory_fraction else 0.8
        target_fraction = max(0.1, min(target_fraction, 1.0))
        # Sanitize environment ratios to avoid invalid low/high values
        high_ratio = target_fraction
        low_ratio = max(0.05, min(high_ratio - 0.05, high_ratio * 0.9))
        os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = f"{high_ratio:.3f}"
        os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] = f"{low_ratio:.3f}"
        try:
            torch.mps.set_per_process_memory_fraction(target_fraction)
            torch.mps.empty_cache()
        except Exception:
            logging.warning("MPS optimization failed; continuing without per-process tuning.")
        else:
            logging.info("MPS memory fraction capped at %.2f", target_fraction)

        # Cap batch size for MPS to avoid OOM
        if "batch_size" in training_cfg:
            training_cfg["batch_size"] = min(32, training_cfg["batch_size"])

    # Gradient accumulation for large hidden sizes
    d_model = model_cfg.get("params", {}).get("d_model", 0)
    if d_model > 512:
        training_cfg.setdefault("gradient_accumulation_steps", 4)
        training_cfg["gradient_accumulation_steps"] = max(4, training_cfg["gradient_accumulation_steps"])


def _apply_runtime_limits(cpu_threads: int, matmul_precision: str) -> None:
    """Apply CPU and matmul runtime limits to avoid system overloads."""
    if cpu_threads and cpu_threads > 0:
        try:
            torch.set_num_threads(cpu_threads)
            torch.set_num_interop_threads(max(1, cpu_threads // 2))
        except Exception:
            logging.warning("Unable to set interop threads; continuing with torch defaults.")
        logging.info("CPU threads capped at %d", cpu_threads)

    if matmul_precision:
        try:
            torch.set_float32_matmul_precision(matmul_precision)
            logging.info("Matmul precision set to %s", matmul_precision)
        except Exception:
            logging.warning("Matmul precision %s not applied; continuing with defaults.", matmul_precision)


def setup_training(
    config_path: str,
    device: str = "auto",
    debug_mode: bool = False,
    fast_dev_run: bool = False,
    use_alternative_data: bool = False,
    cpu_threads: int = 0,
    mps_memory_fraction: float = 0.0,
    matmul_precision: str = "medium",
) -> Any:
    """
    Configure and launch training.

    Args:
        config_path: Path to YAML configuration
        device: 'mps', 'cpu', or 'auto'
        debug_mode: Enable lighter run for debugging
        fast_dev_run: Single-epoch quick sanity check
        cpu_threads: Maximum CPU threads to use (0 = no cap)
        mps_memory_fraction: Fraction of unified memory allowed for MPS (0 = default)
        matmul_precision: torch.set_float32_matmul_precision value
    """
    config = load_config(config_path)
    _apply_runtime_limits(cpu_threads, matmul_precision)

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

    _apply_mps_optimizations(device, training_cfg, model_cfg, mps_memory_fraction if mps_memory_fraction > 0 else None)

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
    parser.add_argument("--cpu_threads", type=int, default=0, help="Nombre maximum de threads CPU (0 = auto)")
    parser.add_argument(
        "--mps_memory_fraction",
        type=float,
        default=0.0,
        help="Fraction maximale de mémoire dédiée au device MPS (0 = défaut PyTorch)",
    )
    parser.add_argument(
        "--matmul_precision",
        type=str,
        default="medium",
        choices=["high", "medium", "low"],
        help="Précision des multiplications matricielles (torch.set_float32_matmul_precision)",
    )
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
        args.cpu_threads,
        args.mps_memory_fraction,
        args.matmul_precision,
    )
