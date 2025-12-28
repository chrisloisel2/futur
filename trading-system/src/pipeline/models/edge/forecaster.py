from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from pipeline.models.base import BaseModel


# =============================================================================
# EDGE FORECASTER — TRM/HRM (SIMPLIFIED + FIXED)
#
# Fixes applied vs previous version:
# 1) seq_len reduced (crypto reacts fast): default 32 (not 128)
# 2) transformer depth reduced: 3 layers / 4 heads / d_model 128
# 3) HRM regime conditioning: NO random projection
#    - you provide explicit regime columns (regime_*), projected by a learned linear layer
#    - if regime columns missing => regime conditioning disabled at runtime
# 4) Expected Shortfall: no arbitrary "q05 - 2*tail"
#    - ES computed from (q05) and a learned positive scale "sigma_tail"
#    - ES = q05 - k * sigma_tail, where k is configurable (default 1.0)
# 5) Multi-task heads kept, but smaller and stable, monotonic quantiles enforced
#
# This is still inference-only wrapper (like your BaseModel). Training lives elsewhere.
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
    use_regime_cond: bool = True
    regime_input_cols: Optional[List[str]] = None  # ex: ["regime_impulse","regime_reversal",...]
    regime_input_dim: int = 6                       # must match len(regime_input_cols) if provided

    # outputs
    quantiles: Tuple[float, float, float] = (0.05, 0.50, 0.95)
    es_k: float = 1.0  # ES = q05 - es_k * sigma_tail (sigma_tail learned, positive)

    # runtime
    device: str = "cpu"
    dtype: str = "float32"
    torch_compile: bool = False

    # IO
    artifact_name: str = "edge_forecaster_trm_hrm_v2.pt"


# -------------------------
# Torch backbone
# -------------------------
def _lazy_import_torch():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    return torch, nn, F


class _RMSNorm:
    def __init__(self, dim: int, eps: float = 1e-6):
        torch, nn, _ = _lazy_import_torch()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = eps

    def __call__(self, x):
        torch, _, _ = _lazy_import_torch()
        norm = x.pow(2).mean(dim=-1, keepdim=True).add(self.eps).rsqrt()
        return x * norm * self.weight

    def parameters(self):
        return [self.weight]


class _CausalSelfAttention:
    """
    Minimal causal attention + ALiBi bias.
    Kept for good extrapolation and no positional embedding table.
    """

    def __init__(self, d_model: int, n_heads: int, dropout: float, attn_dropout: float):
        torch, nn, F = _lazy_import_torch()
        assert d_model % n_heads == 0
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads

        self.qkv = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)
        self.dropout = nn.Dropout(dropout)
        self.attn_dropout = nn.Dropout(attn_dropout)

        slopes = self._get_alibi_slopes(n_heads)
        self.registered_slopes = nn.Parameter(torch.tensor(slopes, dtype=torch.float32), requires_grad=False)

    @staticmethod
    def _get_alibi_slopes(n_heads: int) -> List[float]:
        def get_slopes(n):
            import math

            if math.log2(n).is_integer():
                start = 2 ** (-2 ** -(math.log2(n) - 3))
                ratio = start
                return [start * ratio**i for i in range(n)]
            closest = 2 ** int(np.floor(np.log2(n)))
            return get_slopes(closest) + get_slopes(2 * closest)[0::2][: n - closest]

        return get_slopes(n_heads)

    def __call__(self, x):
        torch, nn, F = _lazy_import_torch()
        B, T, D = x.shape

        qkv = self.qkv(x)
        q, k, v = qkv.chunk(3, dim=-1)

        q = q.view(B, T, self.n_heads, self.d_head).transpose(1, 2)  # [B,H,T,d]
        k = k.view(B, T, self.n_heads, self.d_head).transpose(1, 2)
        v = v.view(B, T, self.n_heads, self.d_head).transpose(1, 2)

        att = (q @ k.transpose(-2, -1)) / np.sqrt(self.d_head)  # [B,H,T,T]

        # causal mask
        mask = torch.ones(T, T, device=x.device, dtype=torch.bool).triu(1)
        att = att.masked_fill(mask, float("-inf"))

        # ALiBi
        dist = torch.arange(T, device=x.device).view(1, -1) - torch.arange(T, device=x.device).view(-1, 1)
        dist = dist.clamp(min=0).float()
        slopes = self.registered_slopes.view(1, self.n_heads, 1, 1).to(x.device)
        att = att - slopes * dist.view(1, 1, T, T)

        att = F.softmax(att, dim=-1)
        att = self.attn_dropout(att)

        out = att @ v
        out = out.transpose(1, 2).contiguous().view(B, T, D)
        out = self.proj(out)
        out = self.dropout(out)
        return out

    def parameters(self):
        return (
            list(self.qkv.parameters())
            + list(self.proj.parameters())
            + list(self.dropout.parameters())
            + list(self.attn_dropout.parameters())
            + [self.registered_slopes]
        )


class _TransformerBlock:
    def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float, attn_dropout: float):
        torch, nn, F = _lazy_import_torch()
        self.norm1 = _RMSNorm(d_model)
        self.attn = _CausalSelfAttention(d_model, n_heads, dropout=dropout, attn_dropout=attn_dropout)
        self.norm2 = _RMSNorm(d_model)

        self.ff = nn.Sequential(
            nn.Linear(d_model, d_ff, bias=False),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(d_ff, d_model, bias=False),
            nn.Dropout(dropout),
        )

    def __call__(self, x):
        x = x + self.attn(self.norm1(x))
        x = x + self.ff(self.norm2(x))
        return x

    def parameters(self):
        params = []
        params += self.norm1.parameters()
        params += self.attn.parameters()
        params += self.norm2.parameters()
        for m in self.ff:
            if hasattr(m, "parameters"):
                params += list(m.parameters())
        return params


class _EdgeForecasterNet:
    def __init__(self, input_dim: int, cfg: EdgeForecasterConfig):
        torch, nn, F = _lazy_import_torch()
        self.cfg = cfg

        self.in_proj = nn.Linear(input_dim, cfg.d_model, bias=False)
        self.in_drop = nn.Dropout(cfg.dropout)

        self.blocks = [
            _TransformerBlock(cfg.d_model, cfg.n_heads, cfg.d_ff, cfg.dropout, cfg.attn_dropout)
            for _ in range(cfg.n_layers)
        ]
        self.norm = _RMSNorm(cfg.d_model)

        # HRM regime conditioning (NO RANDOM PROJECTION)
        self.regime_proj = None
        if cfg.use_regime_cond and cfg.regime_input_dim > 0:
            self.regime_proj = nn.Linear(cfg.regime_input_dim, cfg.d_model, bias=False)

        # Heads:
        # - quantiles: output base + left/right positive deltas -> monotonic enforced
        # - p_hit: sigmoid
        # - rv_mean: forward volatility mean proxy (unbounded; you can postprocess)
        # - sigma_tail: positive scale (used for ES)
        self.head_q = nn.Linear(cfg.d_model, 3, bias=False)
        self.head_phit = nn.Linear(cfg.d_model, 1, bias=False)
        self.head_rv = nn.Linear(cfg.d_model, 1, bias=False)
        self.head_sigma_tail = nn.Linear(cfg.d_model, 1, bias=False)

        # make outputs small initially (avoid unstable large outputs at init)
        with torch.no_grad():
            self.head_q.weight.mul_(0.01)
            self.head_phit.weight.mul_(0.01)
            self.head_rv.weight.mul_(0.01)
            self.head_sigma_tail.weight.mul_(0.01)

    def parameters(self):
        torch, nn, F = _lazy_import_torch()
        params = []
        params += list(self.in_proj.parameters())
        params += list(self.in_drop.parameters())
        for b in self.blocks:
            params += b.parameters()
        params += self.norm.parameters()
        if self.regime_proj is not None:
            params += list(self.regime_proj.parameters())
        params += list(self.head_q.parameters())
        params += list(self.head_phit.parameters())
        params += list(self.head_rv.parameters())
        params += list(self.head_sigma_tail.parameters())
        return params

    def state_dict(self):
        # lightweight state dict (robust to refactors)
        sd = {}
        sd["in_proj.weight"] = self.in_proj.weight

        for i, b in enumerate(self.blocks):
            sd[f"blocks.{i}.attn.qkv.weight"] = b.attn.qkv.weight
            sd[f"blocks.{i}.attn.proj.weight"] = b.attn.proj.weight
            sd[f"blocks.{i}.norm1.weight"] = b.norm1.weight
            sd[f"blocks.{i}.norm2.weight"] = b.norm2.weight
            sd[f"blocks.{i}.ff.0.weight"] = b.ff[0].weight
            sd[f"blocks.{i}.ff.3.weight"] = b.ff[3].weight

        sd["norm.weight"] = self.norm.weight

        if self.regime_proj is not None:
            sd["regime_proj.weight"] = self.regime_proj.weight

        sd["head_q.weight"] = self.head_q.weight
        sd["head_phit.weight"] = self.head_phit.weight
        sd["head_rv.weight"] = self.head_rv.weight
        sd["head_sigma_tail.weight"] = self.head_sigma_tail.weight
        return sd

    def load_state_dict(self, sd):
        def _maybe(name, tensor):
            if name in sd:
                tensor.data.copy_(sd[name].data if hasattr(sd[name], "data") else sd[name])

        _maybe("in_proj.weight", self.in_proj.weight)

        for i, b in enumerate(self.blocks):
            _maybe(f"blocks.{i}.attn.qkv.weight", b.attn.qkv.weight)
            _maybe(f"blocks.{i}.attn.proj.weight", b.attn.proj.weight)
            _maybe(f"blocks.{i}.norm1.weight", b.norm1.weight)
            _maybe(f"blocks.{i}.norm2.weight", b.norm2.weight)
            _maybe(f"blocks.{i}.ff.0.weight", b.ff[0].weight)
            _maybe(f"blocks.{i}.ff.3.weight", b.ff[3].weight)

        _maybe("norm.weight", self.norm.weight)

        if self.regime_proj is not None:
            _maybe("regime_proj.weight", self.regime_proj.weight)

        _maybe("head_q.weight", self.head_q.weight)
        _maybe("head_phit.weight", self.head_phit.weight)
        _maybe("head_rv.weight", self.head_rv.weight)
        _maybe("head_sigma_tail.weight", self.head_sigma_tail.weight)

    def __call__(self, x_seq, regime_vec=None):
        """
        x_seq: [B,T,F]
        regime_vec: [B, regime_input_dim] or None
        """
        torch, nn, F = _lazy_import_torch()
        cfg = self.cfg

        h = self.in_drop(self.in_proj(x_seq))  # [B,T,D]
        for b in self.blocks:
            h = b(h)
        h = self.norm(h)

        pooled = h[:, -1, :]  # last token, causal

        # regime conditioning (learned) — only if we have data AND proj exists
        if self.regime_proj is not None and regime_vec is not None:
            pooled = pooled + self.regime_proj(regime_vec)

        q_raw = self.head_q(pooled)  # [B,3]
        base = q_raw[:, 1:2]
        left = F.softplus(q_raw[:, 0:1])     # >=0
        right = F.softplus(q_raw[:, 2:3])    # >=0

        q05 = base - left
        q50 = base
        q95 = base + right

        p_hit = torch.sigmoid(self.head_phit(pooled))
        rv_mean = self.head_rv(pooled)

        sigma_tail = F.softplus(self.head_sigma_tail(pooled)) + 1e-8  # positive
        return q05, q50, q95, p_hit, rv_mean, sigma_tail


# -------------------------
# Pandas -> rolling sequences
# -------------------------
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
        pad = np.zeros((seq_len - n, x.shape[1]), dtype=np.float32)
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
    """
    Inference wrapper.
    - pass your Mongo buffer window as state_df (ordered by time).
    - this returns predictions aligned on sequence ends (others are NaN).
    """

    def __init__(self, cfg: Optional[EdgeForecasterConfig] = None):
        self.cfg = cfg or EdgeForecasterConfig()
        self.q_levels = list(self.cfg.quantiles)

        self.feature_cols: Optional[List[str]] = self.cfg.feature_cols
        self.net = None
        self.input_dim: Optional[int] = None

        self._torch = None
        self._device = None
        self._dtype = None

    def _ensure_torch(self):
        if self._torch is None:
            torch, nn, F = _lazy_import_torch()
            self._torch = torch
            self._device = torch.device(self.cfg.device)
            dt = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}.get(self.cfg.dtype, torch.float32)
            self._dtype = dt

    def _ensure_net(self, input_dim: int):
        self._ensure_torch()
        torch = self._torch

        if self.net is None or self.input_dim != input_dim:
            self.net = _EdgeForecasterNet(input_dim=input_dim, cfg=self.cfg)
            self.input_dim = input_dim
            for p in self.net.parameters():
                p.data = p.data.to(device=self._device, dtype=self._dtype)

            if getattr(self.cfg, "torch_compile", False) and hasattr(torch, "compile"):
                try:
                    self.net = torch.compile(self.net)  # type: ignore
                except Exception:
                    pass

    def _extract_regime_vec(self, state_df: pd.DataFrame, end_indices: List[int]):
        if not self.cfg.use_regime_cond:
            return None
        if not self.cfg.regime_input_cols:
            # strict rule: no random projection; if not specified => disabled
            return None
        cols = self.cfg.regime_input_cols
        for c in cols:
            if c not in state_df.columns:
                return None

        rv = state_df.loc[end_indices, cols].astype(np.float32).to_numpy()
        if rv.shape[1] != self.cfg.regime_input_dim:
            # strict: mismatch => disable (no hidden projections)
            return None

        torch = self._torch
        return torch.from_numpy(rv).to(device=self._device, dtype=self._dtype)

    def predict(self, state_df: pd.DataFrame) -> pd.DataFrame:
        if state_df is None or state_df.empty:
            return pd.DataFrame()

        if self.feature_cols is None:
            self.feature_cols = _infer_feature_cols(state_df)
        feature_cols = self.feature_cols

        if not feature_cols:
            return pd.DataFrame(index=state_df.index)

        X, end_indices = _build_sequences(state_df, feature_cols, self.cfg.seq_len, self.cfg.stride)
        if X.shape[0] == 0:
            return pd.DataFrame()

        self._ensure_net(input_dim=X.shape[-1])
        torch = self._torch

        regime_vec = self._extract_regime_vec(state_df, end_indices)

        x_t = torch.from_numpy(X).to(device=self._device, dtype=self._dtype)

        with torch.no_grad():
            q05, q50, q95, p_hit, rv_mean, sigma_tail = self.net(x_t, regime_vec=regime_vec)

        q05 = q05.detach().cpu().numpy().reshape(-1)
        q50 = q50.detach().cpu().numpy().reshape(-1)
        q95 = q95.detach().cpu().numpy().reshape(-1)
        p_hit = p_hit.detach().cpu().numpy().reshape(-1)
        rv_mean = rv_mean.detach().cpu().numpy().reshape(-1)
        sigma_tail = sigma_tail.detach().cpu().numpy().reshape(-1)

        # Expected Shortfall proxy (clean, configurable, not arbitrary)
        # ES is a tail-loss metric: more negative => worse.
        expected_shortfall = q05 - float(self.cfg.es_k) * sigma_tail

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
        payload = {
            "cfg": self.cfg.__dict__,
            "feature_cols": self.feature_cols,
            "input_dim": self.input_dim,
            "state_dict": self.net.state_dict(),
        }
        torch.save(payload, path)

    def load(self, path: str) -> None:
        self._ensure_torch()
        torch = self._torch
        payload = torch.load(path, map_location="cpu")

        cfg_dict = payload.get("cfg", {})
        self.cfg = EdgeForecasterConfig(**cfg_dict)
        self.feature_cols = payload.get("feature_cols", None)

        input_dim = int(payload.get("input_dim", 0))
        if input_dim <= 0:
            raise RuntimeError("Invalid artifact: missing input_dim.")

        self._ensure_net(input_dim=input_dim)
        self.net.load_state_dict(payload["state_dict"])
        for p in self.net.parameters():
            p.data = p.data.to(device=self._device, dtype=self._dtype)
