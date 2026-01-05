"""
CANONICAL EDGE FORECASTER ARCHITECTURE (V4.2)
==============================================

This is the SINGLE SOURCE OF TRUTH for EdgeForecasterNet architecture.

CRITICAL: This file is imported by BOTH:
  1. Training script (scripts/train_edge_forecaster.py)
  2. Inference wrapper (pipeline/models/edge/forecaster.py → EdgeForecasterModel)

DO NOT create duplicate implementations. All modifications must happen here.

Key features:
  - Regime conditioning (optional via cfg.use_regime_cond)
  - AMP-safe numerical protections (clamps, masking)
  - Causal self-attention with ALiBi positional bias
  - V4.2 heads: quantile + dir_hit + is_up + rv + sigma_tail
"""

from __future__ import annotations
import torch
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


def _lazy_import_torch():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    return torch, nn, F


# =============================================================================
# Configuration
# =============================================================================
@dataclass
class EdgeForecasterConfig:
    # sequence
    seq_len: int = 32
    stride: int = 1
    feature_cols: Optional[List[str]] = None

    # transformer (TRM)
    d_model: int = 128
    n_heads: int = 4
    n_layers: int = 3
    d_ff: int = 256
    dropout: float = 0.10
    attn_dropout: float = 0.05

    # HRM (regime conditioning)
    use_regime_cond: bool = False  # DEFAULT: disabled for backward compat
    regime_input_cols: Optional[List[str]] = None
    regime_input_dim: int = 6

    # outputs
    quantiles: Tuple[float, float, float] = (0.05, 0.50, 0.95)
    es_k: float = 1.0

    # temperature calibration (optional, loaded from calibration.json)
    temperature_dir_hit: Optional[float] = None
    temperature_up: Optional[float] = None

    # runtime
    device: str = "cpu"
    dtype: str = "float32"
    torch_compile: bool = False

    # IO
    artifact_name: str = "edge_forecaster_v4_2.pt"


# =============================================================================
# Core Components
# =============================================================================
class RMSNorm(_lazy_import_torch()[1].Module):
    """Root Mean Square Layer Normalization"""
    def __init__(self, dim: int, eps: float = 1e-6):
        torch, nn, _ = _lazy_import_torch()
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = eps

    def forward(self, x):
        norm = x.pow(2).mean(dim=-1, keepdim=True).add(self.eps).rsqrt()
        return x * norm * self.weight


class CausalSelfAttention(_lazy_import_torch()[1].Module):
    """
    Causal self-attention with ALiBi positional bias.

    CRITICAL: Uses production-grade implementations:
      - float("-inf") masking (stable across dtypes)
      - Cached causal_mask buffer (reusable across forward passes)
      - ALiBi slopes as persistent buffer
    """

    def __init__(self, d_model: int, n_heads: int, dropout: float, attn_dropout: float):
        torch, nn, F = _lazy_import_torch()
        super().__init__()
        assert d_model % n_heads == 0, f"d_model={d_model} must be divisible by n_heads={n_heads}"

        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads

        self.qkv = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)
        self.dropout = nn.Dropout(dropout)
        self.attn_dropout = nn.Dropout(attn_dropout)

        # ALiBi slopes: persistent buffer (saved in checkpoint)
        slopes = self._get_alibi_slopes(n_heads)
        self.register_buffer(
            "alibi_slopes",
            torch.tensor(slopes, dtype=torch.float32),
            persistent=True,
        )

        # Causal mask: non-persistent buffer (rebuilt on first forward)
        self.register_buffer("causal_mask", torch.empty(0), persistent=False)

    @staticmethod
    def _get_alibi_slopes(n_heads: int) -> List[float]:
        """Generate ALiBi slopes for n_heads (Press et al. 2021)"""
        def get_slopes(n):
            import math
            if math.log2(n).is_integer():
                start = 2 ** (-2 ** -(math.log2(n) - 3))
                ratio = start
                return [start * ratio**i for i in range(n)]
            closest = 2 ** int(np.floor(np.log2(n)))
            return get_slopes(closest) + get_slopes(2 * closest)[0::2][: n - closest]

        return get_slopes(n_heads)

    def _get_mask(self, T: int, device):
        """Get or create causal mask of size T×T"""
        torch, _, _ = _lazy_import_torch()
        if self.causal_mask.numel() == 0 or self.causal_mask.shape[-1] != T:
            mask = torch.ones(T, T, device=device, dtype=torch.bool).triu(1)
            self.causal_mask = mask
        return self.causal_mask

    def forward(self, x):
        torch, nn, F = _lazy_import_torch()
        B, T, D = x.shape

        qkv = self.qkv(x)
        q, k, v = qkv.chunk(3, dim=-1)

        q = q.view(B, T, self.n_heads, self.d_head).transpose(1, 2)
        k = k.view(B, T, self.n_heads, self.d_head).transpose(1, 2)
        v = v.view(B, T, self.n_heads, self.d_head).transpose(1, 2)

        # Attention scores with scaled dot-product
        att = (q @ k.transpose(-2, -1)) * (self.d_head ** -0.5)

        # CRITICAL: Use float("-inf") for stable masking across dtypes (AMP-safe)
        mask = self._get_mask(T, x.device)
        att = att.masked_fill(mask, float("-inf"))

        # ALiBi positional bias
        dist = torch.arange(T, device=x.device).view(1, -1) - torch.arange(T, device=x.device).view(-1, 1)
        dist = dist.clamp(min=0).float()

        # CRITICAL: Convert slopes to att.dtype for dtype consistency in AMP
        slopes = self.alibi_slopes.view(1, self.n_heads, 1, 1).to(dtype=att.dtype, device=x.device)
        att = att - slopes * dist.view(1, 1, T, T)

        att = F.softmax(att, dim=-1)
        att = self.attn_dropout(att)

        out = att @ v
        out = out.transpose(1, 2).contiguous().view(B, T, D)
        out = self.proj(out)
        out = self.dropout(out)
        return out


class TransformerBlock(_lazy_import_torch()[1].Module):
    """Transformer block: LayerNorm → Attention → Add → LayerNorm → FFN → Add"""

    def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float, attn_dropout: float):
        torch, nn, F = _lazy_import_torch()
        super().__init__()

        self.norm1 = RMSNorm(d_model)
        self.attn = CausalSelfAttention(d_model, n_heads, dropout=dropout, attn_dropout=attn_dropout)
        self.norm2 = RMSNorm(d_model)

        self.ff = nn.Sequential(
            nn.Linear(d_model, d_ff, bias=False),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, d_model, bias=False),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        x = x + self.attn(self.norm1(x))
        x = x + self.ff(self.norm2(x))
        return x


# =============================================================================
# Main Network
# =============================================================================
class EdgeForecasterNet(_lazy_import_torch()[1].Module):
    """
    Edge Forecaster V4.2 - Transformer-based market forecaster

    Architecture:
        Input → Linear projection → Transformer blocks → RMSNorm → Pool last token
        → Optional regime conditioning → 5 output heads

    Heads (V4.2):
        1. head_q: quantile forecasts (q05, q50, q95)
        2. head_logits_dir_hit: directional hit probability (logits)
        3. head_rv: realized volatility forecast
        4. head_sigma_tail: tail risk estimate

    Regime conditioning:
        - If cfg.use_regime_cond=True: adds regime_proj(regime_vec) to pooled representation
        - If cfg.use_regime_cond=False: regime_proj is None (checkpoint incompatible)

    CRITICAL: All forward passes apply identical numerical protections (clamps)
              to ensure training/inference parity in AMP mode.
    """

    def __init__(self, input_dim: int, cfg: EdgeForecasterConfig):
        torch, nn, F = _lazy_import_torch()
        super().__init__()
        self.cfg = cfg

        # Input projection
        self.in_proj = nn.Linear(input_dim, cfg.d_model, bias=False)
        self.in_drop = nn.Dropout(cfg.dropout)

        # Transformer backbone
        self.blocks = nn.ModuleList([
            TransformerBlock(cfg.d_model, cfg.n_heads, cfg.d_ff, cfg.dropout, cfg.attn_dropout)
            for _ in range(cfg.n_layers)
        ])
        self.norm = RMSNorm(cfg.d_model)

        # Regime conditioning (optional)
        self.regime_proj = None
        if cfg.use_regime_cond and cfg.regime_input_dim > 0:
            self.regime_proj = nn.Linear(cfg.regime_input_dim, cfg.d_model, bias=False)

        # V4.2 output heads (is_up removed - redundant noise)
        self.head_q = nn.Linear(cfg.d_model, 3, bias=False)
        self.head_logits_dir_hit = nn.Linear(cfg.d_model, 1, bias=False)
        self.head_rv = nn.Linear(cfg.d_model, 1, bias=False)
        self.head_sigma_tail = nn.Linear(cfg.d_model, 1, bias=False)

        # Initialize heads with small weights (stability)
        with torch.no_grad():
            self.head_q.weight.mul_(0.01)
            self.head_logits_dir_hit.weight.mul_(0.01)
            self.head_rv.weight.mul_(0.01)
            self.head_sigma_tail.weight.mul_(0.01)


    @torch.no_grad()
    def predict_full_outputs(self, x_seq, regime_vec=None):
        torch, nn, F = _lazy_import_torch()
        q05, q50, q95, logits_dir, p_dir_hit, rv_mean, sigma_tail = self.forward(x_seq, regime_vec=regime_vec)

        # p_up: probability that return is positive (derived from q50)
        p_up = (q50 > 0).float()

        return {
            "quantile_05": q05.squeeze(-1),
            "quantile_50": q50.squeeze(-1),
            "quantile_95": q95.squeeze(-1),
            "logits_dir": logits_dir.squeeze(-1),
            "p_dir_hit": p_dir_hit.squeeze(-1),
            "p_up": p_up.squeeze(-1),
            "rv_mean": rv_mean.squeeze(-1),
            "sigma_tail": sigma_tail.squeeze(-1),
            # compat keys
            "quantiles": torch.cat([q05, q50, q95], dim=-1),
        }


    def forward(self, x_seq, regime_vec=None):
        """
        Forward pass with numerical protections.

        Args:
            x_seq: (B, T, D_in) input sequences
            regime_vec: (B, D_regime) regime features (optional, only if use_regime_cond=True)

        Returns:
            Tuple of 7 tensors:
                q05, q50, q95: (B, 1) quantile forecasts
                logits_dir: (B, 1) raw logits for directional hit
                p_dir_hit: (B, 1) sigmoid(logits_dir)
                rv_mean: (B, 1) realized volatility forecast
                sigma_tail: (B, 1) tail risk estimate

        CRITICAL: This forward pass must be IDENTICAL in training and inference.
                  All clamps are applied at the same locations with same thresholds.
        """
        torch, nn, F = _lazy_import_torch()

        # Transformer backbone
        h = self.in_drop(self.in_proj(x_seq))
        for b in self.blocks:
            h = b(h)
        h = self.norm(h)

        # Pool last token
        pooled = h[:, -1, :]

        # Optional regime conditioning
        if self.regime_proj is not None and regime_vec is not None:
            pooled = pooled + self.regime_proj(regime_vec)

        # === HEAD 1: Quantile forecasts ===
        q_raw = self.head_q(pooled)

        # CRITICAL: Clamp before softplus to prevent explosion in AMP
        q_raw = torch.clamp(q_raw, min=-50.0, max=50.0)

        base = q_raw[:, 1:2]
        left = F.softplus(q_raw[:, 0:1])
        right = F.softplus(q_raw[:, 2:3])

        # CRITICAL: Clamp softplus outputs
        left = torch.clamp(left, max=50.0)
        right = torch.clamp(right, max=50.0)

        q05 = base - left
        q50 = base
        q95 = base + right

        # === HEAD 2: Directional hit ===
        logits_dir = self.head_logits_dir_hit(pooled)
        logits_dir = torch.clamp(logits_dir, min=-50.0, max=50.0)
        p_dir_hit = torch.sigmoid(logits_dir)

        # === HEAD 3: Realized volatility ===
        rv_mean = self.head_rv(pooled)
        # No clamp here (can be negative in principle)

        # === HEAD 4: Tail risk ===
        sigma_tail = F.softplus(self.head_sigma_tail(pooled)) + 1e-8
        sigma_tail = torch.clamp(sigma_tail, max=50.0)

        # Return 7 outputs (V4.2 - is_up removed)
        return q05, q50, q95, logits_dir, p_dir_hit, rv_mean, sigma_tail


    def compute_loss(self, x_seq, targets, label_smoothing: float = 0.0, regime_vec=None):
        """
        Compute training loss.

        Args:
            x_seq: (B, T, D_in) input sequences
            targets: (B, 5) tensor with [return_fwd, dir_hit, is_up, is_tp_up_hit, rv_fwd_mean]
            label_smoothing: float, label smoothing for BCE loss
            regime_vec: optional regime conditioning

        Returns:
            total_loss: scalar tensor
        """
        torch, nn, F = _lazy_import_torch()

        # Forward pass
        q05, q50, q95, logits_dir, _p_dir, rv_mean, _sigma_tail = self.forward(x_seq, regime_vec=regime_vec)

        # Sanitize targets
        targets = torch.nan_to_num(targets, nan=0.0, posinf=0.0, neginf=0.0)

        return_fwd = targets[:, 0:1].clamp(-1.0, 1.0)  # 1%/99% - less destructive
        dir_hit = targets[:, 1:2].clamp(0.0, 1.0)
        rv_fwd_mean = targets[:, 4:5].clamp(0.0, 1.0)  # 1%/99% - less destructive

        # Quantile losses
        def quantile_loss(pred, target, tau):
            pred = torch.nan_to_num(pred, nan=0.0, posinf=0.0, neginf=0.0).clamp(-1.0, 1.0)
            target = torch.nan_to_num(target, nan=0.0, posinf=0.0, neginf=0.0).clamp(-1.0, 1.0)
            err = target - pred
            return torch.mean(torch.max(tau * err, (tau - 1.0) * err))

        loss_q05 = quantile_loss(q05, return_fwd, 0.05)
        loss_q50 = quantile_loss(q50, return_fwd, 0.50)
        loss_q95 = quantile_loss(q95, return_fwd, 0.95)

        # Directional hit (BCE with label smoothing)
        if label_smoothing > 0:
            dir_hit_smooth = dir_hit * (1 - label_smoothing) + 0.5 * label_smoothing
        else:
            dir_hit_smooth = dir_hit

        logits_dir_clamp = torch.clamp(logits_dir, -30.0, 30.0)
        loss_dir = F.binary_cross_entropy_with_logits(logits_dir_clamp, dir_hit_smooth)

        # RV loss
        rv_mean_clamp = torch.nan_to_num(rv_mean, nan=0.0, posinf=0.0, neginf=0.0).clamp(0.0, 1.0)
        loss_rv = F.mse_loss(rv_mean_clamp, rv_fwd_mean)

        # Weighted combination
        w_q05 = 0.20
        w_q50 = 0.20
        w_q95 = 0.20
        w_dir = 0.33
        w_rv = 0.07

        total_loss = (
            w_q05 * loss_q05
            + w_q50 * loss_q50
            + w_q95 * loss_q95
            + w_dir * loss_dir
            + w_rv * loss_rv
        )

        return total_loss
