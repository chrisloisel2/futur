"""
Unified artifact management for EdgeForecaster.
Single source of truth for save/load with strict validation + backward compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import torch


@dataclass
class EdgeArtifact:
    version: str
    cfg: Dict[str, Any]
    feature_cols: List[str]
    input_dim: int
    state_dict: Dict[str, Any]
    calibration: Dict[str, Any]
    metadata: Dict[str, Any]


def save_artifact(
    path: str,
    net: torch.nn.Module,
    cfg: Any,
    feature_cols: List[str],
    calibration: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if metadata is None:
        metadata = {}

    if not hasattr(cfg, "__dict__"):
        raise ValueError("cfg must be a dataclass-like object with __dict__")

    # required arch attrs
    for attr in ("d_model", "n_heads", "n_layers", "d_ff", "use_regime_cond"):
        if attr not in cfg.__dict__:
            raise ValueError(f"cfg missing required attribute: {attr}")

    sd = net.state_dict()
    state_keys = set(sd.keys())

    required_heads = {
        "head_q.weight",
        "head_logits_dir_hit.weight",
        "head_rv.weight",
        "head_sigma_tail.weight",
    }
    missing = [k for k in required_heads if k not in state_keys]
    if missing:
        raise ValueError(f"state_dict missing required keys: {missing}")

    if any(k.startswith("head_logits_up.") for k in state_keys):
        raise ValueError("Checkpoint contains deprecated head_logits_up.* (retrain v4.2 clean)")

    # validate input dim vs in_proj
    if "in_proj.weight" in sd:
        actual_input_dim = sd["in_proj.weight"].shape[1]
        if actual_input_dim != len(feature_cols):
            raise ValueError(
                f"input_dim mismatch: in_proj expects {actual_input_dim} but feature_cols={len(feature_cols)}"
            )

    artifact = EdgeArtifact(
        version="edge_forecaster_artifact_v1",
        cfg=cfg.__dict__,
        feature_cols=list(feature_cols),
        input_dim=len(feature_cols),
        state_dict=sd,
        calibration=calibration or {"temperature_dir_hit": None, "temperature_up": None},
        metadata=metadata,
    )

    torch.save(asdict(artifact), path)


def load_artifact(
    path: str,
    device: str = "cpu",
) -> Tuple[Any, Dict[str, Any]]:
    """
    Returns:
        (model, artifact_dict)

    model is an initialized EdgeForecasterModel with loaded weights, cfg, feature_cols.
    artifact_dict contains: calibration, metadata
    """
    from pipeline.models.edge.net import EdgeForecasterConfig
    from pipeline.models.edge.forecaster import EdgeForecasterModel

    payload = torch.load(path, map_location="cpu", weights_only=False)

    # ---- normalize formats ----
    if payload.get("version") == "edge_forecaster_artifact_v1":
        cfg_dict = payload["cfg"]
        feature_cols = payload["feature_cols"]
        input_dim = payload["input_dim"]
        state_dict = payload["state_dict"]
        calibration = payload.get("calibration", {"temperature_dir_hit": None, "temperature_up": None})
        metadata = payload.get("metadata", {})

    elif "cfg" in payload and "state_dict" in payload:
        # legacy v4.2 training script format
        cfg_dict = payload["cfg"]
        feature_cols = payload.get("feature_cols")
        input_dim = payload.get("input_dim")
        state_dict = payload["state_dict"]
        calibration = payload.get("calibration", {"temperature_dir_hit": None, "temperature_up": None})
        metadata = payload.get("notes", payload.get("metadata", {}))

        if feature_cols is None:
            raise RuntimeError("Legacy artifact missing feature_cols")
        if input_dim is None:
            input_dim = len(feature_cols)

    elif "config" in payload and "model_state_dict" in payload:
        # very old format
        cfg_dict = payload["config"]
        feature_cols = payload.get("feature_cols")
        input_dim = payload.get("input_dim")
        state_dict = payload["model_state_dict"]
        calibration = payload.get("calibration", {"temperature_dir_hit": None, "temperature_up": None})
        metadata = payload.get("metadata", {})

        if feature_cols is None:
            raise RuntimeError("Very old artifact missing feature_cols")
        if input_dim is None:
            input_dim = len(feature_cols)

    else:
        raise RuntimeError(f"Unknown artifact format. Keys: {list(payload.keys())}")

    # ---- build cfg ----
    cfg = EdgeForecasterConfig(**cfg_dict)
    cfg.device = device

    # ---- strict validation from state_dict ----
    keys = set(state_dict.keys())

    if "head_q.weight" not in state_dict:
        raise RuntimeError("Invalid checkpoint: missing head_q.weight")

    actual_d_model = state_dict["head_q.weight"].shape[1]
    if cfg.d_model != actual_d_model:
        raise RuntimeError(f"d_model mismatch: cfg={cfg.d_model}, ckpt={actual_d_model}")

    block_indices = sorted({int(k.split(".")[1]) for k in keys if k.startswith("blocks.") and k.split(".")[1].isdigit()})
    if not block_indices:
        raise RuntimeError("Invalid checkpoint: no transformer blocks detected")

    actual_n_layers = max(block_indices) + 1
    if cfg.n_layers != actual_n_layers:
        raise RuntimeError(f"n_layers mismatch: cfg={cfg.n_layers}, ckpt={actual_n_layers}")

    if "blocks.0.attn.alibi_slopes" not in state_dict:
        raise RuntimeError("Invalid checkpoint: missing blocks.0.attn.alibi_slopes")

    actual_n_heads = state_dict["blocks.0.attn.alibi_slopes"].shape[0]
    if cfg.n_heads != actual_n_heads:
        raise RuntimeError(f"n_heads mismatch: cfg={cfg.n_heads}, ckpt={actual_n_heads}")

    has_regime_proj = any(k.startswith("regime_proj.") for k in keys)
    if cfg.use_regime_cond and not has_regime_proj:
        raise RuntimeError("Regime mismatch: cfg.use_regime_cond=True but no regime_proj.* in checkpoint")
    if not cfg.use_regime_cond and has_regime_proj:
        raise RuntimeError("Regime mismatch: cfg.use_regime_cond=False but regime_proj.* exists in checkpoint")

    # Remove deprecated is_up head
    if any(k.startswith("head_logits_up.") for k in keys):
        state_dict = {k: v for k, v in state_dict.items() if not k.startswith("head_logits_up.")}

    # Remap legacy head names to current naming convention
    # Legacy: head_phit -> Current: head_logits_dir_hit
    legacy_remap = {
        "head_phit.weight": "head_logits_dir_hit.weight",
        "head_phit.bias": "head_logits_dir_hit.bias",
    }

    remapped_state_dict = {}
    for k, v in state_dict.items():
        new_key = legacy_remap.get(k, k)
        remapped_state_dict[new_key] = v

    state_dict = remapped_state_dict

    if len(feature_cols) != int(input_dim):
        raise RuntimeError(f"feature_cols len={len(feature_cols)} != input_dim={input_dim}")

    # ---- build model wrapper (single entrypoint) ----
    model = EdgeForecasterModel(cfg=cfg)
    model.feature_cols = list(feature_cols)
    model._ensure_net(input_dim=int(input_dim), compile_net=False)
    assert model.net is not None
    model.net.load_state_dict(state_dict, strict=True)
    model.net.to(device=torch.device(device))
    model.net.eval()

    artifact_dict = {"calibration": calibration, "metadata": metadata}
    return model, artifact_dict
