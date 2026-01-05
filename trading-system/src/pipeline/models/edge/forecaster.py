# =========================
# FILE: src/pipeline/models/edge/forecaster.py
# REFACTORED: Import canonical architecture from net.py (single source of truth)
# =========================
from __future__ import annotations

import os
from typing import List, Optional

import numpy as np
import pandas as pd

from pipeline.models.base import BaseModel
from pipeline.models.edge.net import (
    EdgeForecasterConfig,
    EdgeForecasterNet,
)



def _lazy_import_torch():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    return torch, nn, F


# =============================================================================
# Pandas -> rolling sequences
# =============================================================================
def _infer_feature_cols(df: pd.DataFrame) -> List[str]:
    cols = df.select_dtypes(include="number").columns.tolist()
    drop = {"event_time", "timestamp", "ts"}
    return [c for c in cols if c not in drop and not c.startswith("regime_")]


def _build_sequences(df: pd.DataFrame, feature_cols: List[str], seq_len: int, stride: int):
    if df.empty:
        return np.zeros((0, seq_len, len(feature_cols)), dtype=np.float32), []

    x = df[feature_cols].astype(np.float32).to_numpy()
    n = x.shape[0]
    if n < seq_len:
        # CRITICAL FIX: Repeat first bar instead of zero-padding
        # Zero-padding fausses les statistics (momentum, volatility)
        pad = np.repeat(x[0:1], seq_len - n, axis=0)
        seq = np.concatenate([pad, x], axis=0)[None, :, :]
        return seq, [df.index[-1]]

    ends = list(range(seq_len - 1, n, stride))
    X = np.zeros((len(ends), seq_len, x.shape[1]), dtype=np.float32)
    for i, e in enumerate(ends):
        X[i] = x[e - seq_len + 1 : e + 1]
    return X, [df.index[e] for e in ends]


# =============================================================================
# PUBLIC WRAPPER
# =============================================================================
class EdgeForecasterModel(BaseModel):
    def __init__(self, cfg: Optional[EdgeForecasterConfig] = None):
        self.cfg = cfg or EdgeForecasterConfig()
        self.q_levels = list(self.cfg.quantiles)

        self.feature_cols: Optional[List[str]] = self.cfg.feature_cols
        self.net: Optional[EdgeForecasterNet] = None
        self.input_dim: Optional[int] = None

        self._torch = None
        self._device = None
        self._dtype = None

        self._compiled = False

    def _ensure_torch(self):
        if self._torch is None:
            torch, _, _ = _lazy_import_torch()
            self._torch = torch
            self._device = torch.device(self.cfg.device)
            self._dtype = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}.get(
                self.cfg.dtype, torch.float32
            )

    def _maybe_compile(self):
        if self.net is None:
            return
        if self._compiled:
            return
        torch = self._torch
        if getattr(self.cfg, "torch_compile", False) and hasattr(torch, "compile"):
            try:
                # compile only once, and never save compiled modules
                self.net = torch.compile(self.net)  # type: ignore
                self._compiled = True
            except Exception:
                pass

    def _ensure_net(self, input_dim: int, compile_net: bool = False):
        self._ensure_torch()
        if self.net is None or self.input_dim != input_dim:
            self.net = EdgeForecasterNet(input_dim=input_dim, cfg=self.cfg)
            self.input_dim = input_dim
            self.net.to(device=self._device, dtype=self._dtype)
            self._compiled = False

        if compile_net:
            self._maybe_compile()

    def _extract_regime_vec(self, state_df: pd.DataFrame, end_indices):
        if not self.cfg.use_regime_cond:
            return None
        if not self.cfg.regime_input_cols:
            return None
        cols = self.cfg.regime_input_cols
        for c in cols:
            if c not in state_df.columns:
                return None

        rv = state_df.loc[end_indices, cols].astype(np.float32).to_numpy()
        if rv.shape[1] != self.cfg.regime_input_dim:
            return None

        torch = self._torch
        return torch.from_numpy(rv).to(device=self._device, dtype=self._dtype)

    def predict(self, state_df: pd.DataFrame, return_full: bool = False) -> pd.DataFrame:
        """
        Generate predictions from input state.

        Args:
            state_df: Input DataFrame with feature columns
            return_full: If True, return all outputs including p_up, logits_dir, sigma_tail.
                        If False (default), return only 6 core outputs for backward compatibility.

        Returns:
            DataFrame with predictions aligned to state_df.index.

            Standard output (return_full=False): 6 columns
                - q05, q50, q95: quantile forecasts
                - p_hit: probability of directional hit (from p_dir_hit)
                - expected_shortfall: downside risk estimate
                - rv_mean: realized volatility mean

            Full output (return_full=True): 9 columns (standard + 3 additional)
                - p_up: probability of upward movement (derived from q50 > 0)
                - logits_dir: raw logits for directional hit
                - sigma_tail: tail risk standard deviation
        """
        if state_df is None or state_df.empty:
            return pd.DataFrame()

        if self.feature_cols is None:
            self.feature_cols = _infer_feature_cols(state_df)
        feature_cols = self.feature_cols

        if not feature_cols:
            return pd.DataFrame(index=state_df.index)

        missing = [c for c in feature_cols if c not in state_df.columns]
        if missing:
            raise KeyError(f"Missing features in input data: {missing}. Expected: {feature_cols}")

        X, end_indices = _build_sequences(state_df, feature_cols, self.cfg.seq_len, self.cfg.stride)
        if X.shape[0] == 0:
            return pd.DataFrame()

        # compile ok for inference only
        self._ensure_net(input_dim=X.shape[-1], compile_net=self.cfg.torch_compile)

        torch = self._torch
        regime_vec = self._extract_regime_vec(state_df, end_indices)
        x_t = torch.from_numpy(X).to(device=self._device, dtype=self._dtype)

        self.net.eval()
        with torch.no_grad():
            # V4.2: unpack 7 outputs (is_up removed)
            q05, q50, q95, logits_dir, p_dir_hit, rv_mean, sigma_tail = self.net(x_t, regime_vec=regime_vec)

            # Apply temperature scaling if configured
            if self.cfg.temperature_dir_hit is not None and self.cfg.temperature_dir_hit > 0:
                # Re-apply sigmoid with temperature scaling: p = sigmoid(logits / T)
                p_dir_hit = torch.sigmoid(logits_dir / self.cfg.temperature_dir_hit)

        q05 = q05.detach().float().cpu().numpy().reshape(-1)
        q50 = q50.detach().float().cpu().numpy().reshape(-1)
        q95 = q95.detach().float().cpu().numpy().reshape(-1)
        p_hit = p_dir_hit.detach().float().cpu().numpy().reshape(-1)  # Use p_dir_hit (temperature-scaled)
        logits_dir_arr = logits_dir.detach().float().cpu().numpy().reshape(-1)
        rv_mean = rv_mean.detach().float().cpu().numpy().reshape(-1)
        sigma_tail_arr = sigma_tail.detach().float().cpu().numpy().reshape(-1)

        # Derive p_up from q50 sign (backward compat)
        p_up_arr = (q50 > 0).astype(np.float32)

        expected_shortfall = q05 - float(self.cfg.es_k) * sigma_tail_arr

        # Build output based on return_full flag
        if return_full:
            out = pd.DataFrame(
                {
                    "q05": q05,
                    "q50": q50,
                    "q95": q95,
                    "p_hit": np.clip(p_hit, 0.0, 1.0),
                    "expected_shortfall": expected_shortfall,
                    "rv_mean": rv_mean,
                    "p_up": np.clip(p_up_arr, 0.0, 1.0),  # Derived from q50 sign
                    "logits_dir": logits_dir_arr,
                    "sigma_tail": sigma_tail_arr,
                },
                index=end_indices,
            )
        else:
            out = pd.DataFrame(
                {
                    "q05": q05,
                    "q50": q50,
                    "q95": q95,
                    "p_hit": np.clip(p_hit, 0.0, 1.0),
                    "expected_shortfall": expected_shortfall,
                    "rv_mean": rv_mean,
                },
                index=end_indices,
            )

        aligned = pd.DataFrame(index=state_df.index, columns=out.columns, dtype=np.float32)
        aligned.loc[out.index] = out
        return aligned

    def save(self, path: str) -> None:
        self._ensure_torch()
        torch = self._torch
        if self.net is None or self.input_dim is None:
            raise RuntimeError("Model not initialized. Run predict() once or call load() first.")

        os.makedirs(os.path.dirname(path), exist_ok=True)

        # never save compiled module wrapper
        state_dict = self.net.state_dict()

        payload = {
            "cfg": self.cfg.__dict__,
            "feature_cols": self.feature_cols,
            "input_dim": self.input_dim,
            "state_dict": state_dict,
        }
        torch.save(payload, path)

    def load(self, path: str) -> None:
        self._ensure_torch()
        torch = self._torch

        # FIX: always load on CPU, then move to device
        payload = torch.load(path, map_location="cpu", weights_only=False)

        cfg_dict = payload.get("cfg", {})
        if not cfg_dict:
            raise RuntimeError("Invalid artifact: missing cfg in payload.")

        # Load checkpoint config (will be validated against current self.cfg)
        checkpoint_cfg = EdgeForecasterConfig(**cfg_dict)

        input_dim = int(payload.get("input_dim", 0))
        if input_dim <= 0:
            raise RuntimeError("Invalid artifact: missing input_dim.")

        # =====================================================================
        # VALIDATION 1: Architecture dimensions (checkpoint vs current cfg)
        # =====================================================================
        state_dict = payload["state_dict"]
        state_keys = set(state_dict.keys())

        # Extract architecture dimensions from state_dict and validate
        errors = []

        # 1. Validate d_model from head_q.weight shape [3, d_model]
        if "head_q.weight" in state_dict:
            actual_d_model = state_dict["head_q.weight"].shape[1]
            if checkpoint_cfg.d_model != actual_d_model:
                errors.append(
                    f"d_model mismatch: checkpoint cfg={checkpoint_cfg.d_model}, "
                    f"state_dict head_q.weight shape={state_dict['head_q.weight'].shape} implies d_model={actual_d_model}"
                )
        else:
            errors.append("Missing head_q.weight in state_dict - cannot validate d_model")

        # 2. Validate n_layers from number of transformer blocks
        block_indices = set()
        for key in state_keys:
            if key.startswith("blocks."):
                # Extract block index: blocks.0.norm1.weight -> 0
                parts = key.split(".")
                if len(parts) >= 2 and parts[1].isdigit():
                    block_indices.add(int(parts[1]))

        if block_indices:
            actual_n_layers = max(block_indices) + 1
            if checkpoint_cfg.n_layers != actual_n_layers:
                errors.append(
                    f"n_layers mismatch: checkpoint cfg={checkpoint_cfg.n_layers}, "
                    f"state_dict has blocks {sorted(block_indices)} implies n_layers={actual_n_layers}"
                )
        else:
            errors.append("No transformer blocks found in state_dict - cannot validate n_layers")

        # 3. Validate n_heads from alibi_slopes buffer shape [n_heads]
        if "blocks.0.attn.alibi_slopes" in state_dict:
            actual_n_heads = state_dict["blocks.0.attn.alibi_slopes"].shape[0]
            if checkpoint_cfg.n_heads != actual_n_heads:
                errors.append(
                    f"n_heads mismatch: checkpoint cfg={checkpoint_cfg.n_heads}, "
                    f"state_dict alibi_slopes shape={state_dict['blocks.0.attn.alibi_slopes'].shape} implies n_heads={actual_n_heads}"
                )
        else:
            errors.append("Missing blocks.0.attn.alibi_slopes in state_dict - cannot validate n_heads")

        # 4. Validate d_ff from first feedforward layer shape [d_ff, d_model]
        if "blocks.0.ff.0.weight" in state_dict:
            actual_d_ff = state_dict["blocks.0.ff.0.weight"].shape[0]
            if checkpoint_cfg.d_ff != actual_d_ff:
                errors.append(
                    f"d_ff mismatch: checkpoint cfg={checkpoint_cfg.d_ff}, "
                    f"state_dict blocks.0.ff.0.weight shape={state_dict['blocks.0.ff.0.weight'].shape} implies d_ff={actual_d_ff}"
                )

        # Fail hard if any mismatches detected
        if errors:
            error_msg = "Configuration integrity check FAILED:\n" + "\n".join(f"  - {e}" for e in errors)
            error_msg += f"\n\nCheckpoint cfg: d_model={checkpoint_cfg.d_model}, n_heads={checkpoint_cfg.n_heads}, "
            error_msg += f"n_layers={checkpoint_cfg.n_layers}, d_ff={checkpoint_cfg.d_ff}"
            error_msg += f"\n\nThis usually means the checkpoint was trained with different hyperparameters."
            error_msg += f"\nDo NOT fallback to defaults - the cfg must exactly match the trained model."
            raise RuntimeError(error_msg)

        # =====================================================================
        # VALIDATION 2: Regime conditioning compatibility (NEW)
        # =====================================================================
        has_regime_proj = any(k.startswith("regime_proj.") for k in state_keys)

        if checkpoint_cfg.use_regime_cond and not has_regime_proj:
            raise RuntimeError(
                f"REGIME CONDITIONING MISMATCH:\n"
                f"  checkpoint cfg.use_regime_cond=True but checkpoint has NO regime_proj weights.\n"
                f"  This checkpoint was trained WITHOUT regime conditioning.\n"
                f"\n"
                f"  Solutions:\n"
                f"    1. Load with cfg.use_regime_cond=False (TRM-only mode)\n"
                f"    2. Retrain model with regime conditioning enabled\n"
                f"\n"
                f"  Checkpoint keys: {sorted([k for k in state_keys if 'regime' in k or k.startswith('head_')])}"
            )

        if not checkpoint_cfg.use_regime_cond and has_regime_proj:
            raise RuntimeError(
                f"REGIME CONDITIONING MISMATCH:\n"
                f"  checkpoint cfg.use_regime_cond=False but checkpoint HAS regime_proj weights.\n"
                f"  This checkpoint was trained WITH regime conditioning.\n"
                f"\n"
                f"  Solutions:\n"
                f"    1. Load with cfg.use_regime_cond=True (TRM+HRM mode)\n"
                f"    2. Train new TRM-only model from scratch\n"
                f"\n"
                f"  Checkpoint keys: {sorted([k for k in state_keys if 'regime' in k])}"
            )

        # =====================================================================
        # VALIDATION 3: User-provided cfg vs checkpoint cfg compatibility
        # =====================================================================
        # If user provided a cfg at construction, validate it matches checkpoint
        if self.cfg is not None:
            user_cfg = self.cfg
            arch_errors = []

            if user_cfg.d_model != checkpoint_cfg.d_model:
                arch_errors.append(f"d_model: user={user_cfg.d_model}, checkpoint={checkpoint_cfg.d_model}")
            if user_cfg.n_heads != checkpoint_cfg.n_heads:
                arch_errors.append(f"n_heads: user={user_cfg.n_heads}, checkpoint={checkpoint_cfg.n_heads}")
            if user_cfg.n_layers != checkpoint_cfg.n_layers:
                arch_errors.append(f"n_layers: user={user_cfg.n_layers}, checkpoint={checkpoint_cfg.n_layers}")
            if user_cfg.d_ff != checkpoint_cfg.d_ff:
                arch_errors.append(f"d_ff: user={user_cfg.d_ff}, checkpoint={checkpoint_cfg.d_ff}")
            if user_cfg.use_regime_cond != checkpoint_cfg.use_regime_cond:
                arch_errors.append(
                    f"use_regime_cond: user={user_cfg.use_regime_cond}, checkpoint={checkpoint_cfg.use_regime_cond}"
                )

            if arch_errors:
                raise RuntimeError(
                    f"USER CONFIG vs CHECKPOINT MISMATCH:\n"
                    f"  You provided a config at construction that doesn't match the checkpoint.\n"
                    f"\n"
                    f"  Mismatches:\n"
                    + "\n".join(f"    - {e}" for e in arch_errors)
                    + f"\n\n"
                    f"  Solutions:\n"
                    f"    1. Let load() auto-detect config: EdgeForecasterModel().load(path)\n"
                    f"    2. Provide matching config: EdgeForecasterModel(cfg=checkpoint_cfg).load(path)\n"
                )

        # All validations passed - use checkpoint config
        self.cfg = checkpoint_cfg
        self.feature_cols = payload.get("feature_cols", None)

        print(f"[LOAD] ✓ Config validation passed: d_model={self.cfg.d_model}, n_heads={self.cfg.n_heads}, "
              f"n_layers={self.cfg.n_layers}, d_ff={self.cfg.d_ff}, use_regime_cond={self.cfg.use_regime_cond}")

        # do not compile at load time
        self._ensure_net(input_dim=input_dim, compile_net=False)

        assert self.net is not None

        # Check for v4.2 native architecture (dir_hit only, is_up removed)
        has_dir_hit = any(k.startswith("head_logits_dir_hit.") for k in state_keys)
        has_up = any(k.startswith("head_logits_up.") for k in state_keys)
        has_legacy_phit = any(k.startswith("head_phit.") for k in state_keys)

        if has_dir_hit and not has_up:
            # Path 1: Native v4.2 model (is_up removed) - strict loading
            print(f"[LOAD] Detected native v4.2 architecture (head_logits_dir_hit, is_up removed)")
            self.net.load_state_dict(state_dict, strict=True)
            print(f"[LOAD] ✓ Successfully loaded {len(state_dict)} keys in strict mode (native_v42_no_is_up)")

        elif has_dir_hit and has_up:
            # Path 2: Legacy v4.2 with is_up (remove it)
            print(f"[LOAD] Detected legacy v4.2 architecture (with is_up) - removing is_up head")
            filtered_dict = {k: v for k, v in state_dict.items() if not k.startswith("head_logits_up.")}
            self.net.load_state_dict(filtered_dict, strict=False)
            print(f"[LOAD] ✓ Loaded {len(filtered_dict)} keys (removed is_up head)")

        elif has_legacy_phit:
            # Path 3: Legacy model - convert head_phit to v4.2
            print(f"[LOAD] Detected legacy architecture (head_phit) - converting to v4.2")

            # Rename head_phit.* → head_logits_dir_hit.*
            converted_dict = {}
            for k, v in state_dict.items():
                if k.startswith("head_phit."):
                    new_key = k.replace("head_phit.", "head_logits_dir_hit.")
                    converted_dict[new_key] = v
                    print(f"[LOAD]   Renamed: {k} → {new_key}")
                else:
                    converted_dict[k] = v

            # Load with strict=False (is_up head will be randomly initialized)
            self.net.load_state_dict(converted_dict, strict=False)
            print(f"[LOAD] ✓ Successfully loaded {len(converted_dict)} keys (legacy_convert)")
            print(f"[LOAD] ⚠  Warning: retrain recommended")

        else:
            # Path 4: Invalid/unknown architecture
            expected_keys = [
                "head_logits_dir_hit.weight",
                "head_q.weight",
                "head_rv.weight",
                "head_sigma_tail.weight"
            ]
            missing_keys = [k for k in expected_keys if k not in state_keys]

            raise RuntimeError(
                f"Invalid checkpoint: Cannot detect v4.2 or legacy architecture.\n"
                f"Expected v4.2 keys: {expected_keys}\n"
                f"Or legacy key: head_phit.weight\n"
                f"Missing keys: {missing_keys}\n"
                f"Available head keys: {[k for k in state_keys if k.startswith('head_')]}"
            )

        self.net.to(device=self._device, dtype=self._dtype)
        self._compiled = False
