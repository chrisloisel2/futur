"""
Advanced fusion mechanism for combining time series and tabular embeddings.

Features:
- Cross-attention between branches
- Adaptive gating based on market regime
- Meta-features (volatility, trend, correlation)
- Learnable fusion weights
- Advanced layer normalization
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, Optional, Tuple, List
import numpy as np


class MarketRegimeDetector(nn.Module):
    """
    Detect market regime from meta-features.

    Regimes:
    - Trending (high momentum, low volatility)
    - Mean-reverting (low momentum, medium volatility)
    - Volatile (high volatility)
    - Stable (low volatility, low momentum)
    """

    def __init__(self, meta_feature_dim: int = 3, hidden_dim: int = 64):
        """
        Args:
            meta_feature_dim: Number of meta-features (volatility, trend, correlation)
            hidden_dim: Hidden dimension for regime detection
        """
        super().__init__()

        self.meta_feature_dim = meta_feature_dim

        # MLP for regime detection
        self.regime_detector = nn.Sequential(
            nn.Linear(meta_feature_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim // 2, 4),  # 4 regimes
        )

    def forward(self, meta_features: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Detect market regime.

        Args:
            meta_features: [batch, meta_feature_dim] containing:
                - volatility (rolling std of returns)
                - trend (rolling mean of returns)
                - correlation (rolling correlation with market)

        Returns:
            regime_logits: [batch, 4] logits for each regime
            regime_probs: [batch, 4] probabilities for each regime
        """
        regime_logits = self.regime_detector(meta_features)
        regime_probs = F.softmax(regime_logits, dim=-1)

        return regime_logits, regime_probs


class CrossBranchAttention(nn.Module):
    """
    Cross-attention between different model branches.

    Allows each branch to attend to others for information fusion.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int = 8,
        dropout: float = 0.1,
    ):
        """
        Args:
            d_model: Model dimension
            n_heads: Number of attention heads
            dropout: Dropout probability
        """
        super().__init__()

        assert d_model % n_heads == 0, "d_model must be divisible by n_heads"

        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads

        # Q, K, V projections
        self.W_q = nn.Linear(d_model, d_model)
        self.W_k = nn.Linear(d_model, d_model)
        self.W_v = nn.Linear(d_model, d_model)

        # Output projection
        self.W_o = nn.Linear(d_model, d_model)

        self.dropout = nn.Dropout(dropout)
        self.layer_norm = nn.LayerNorm(d_model)

    def forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Cross-attention between branches.

        Args:
            query: [batch, d_model] from one branch
            key: [batch, d_model] from another branch
            value: [batch, d_model] from another branch
            mask: Optional attention mask

        Returns:
            output: [batch, d_model] attended representation
        """
        batch_size = query.size(0)

        # Add sequence dimension if needed
        if query.dim() == 2:
            query = query.unsqueeze(1)  # [batch, 1, d_model]
            key = key.unsqueeze(1)
            value = value.unsqueeze(1)
            squeeze_output = True
        else:
            squeeze_output = False

        residual = query

        # Project and reshape for multi-head attention
        Q = self.W_q(query).view(batch_size, -1, self.n_heads, self.d_k).transpose(1, 2)
        K = self.W_k(key).view(batch_size, -1, self.n_heads, self.d_k).transpose(1, 2)
        V = self.W_v(value).view(batch_size, -1, self.n_heads, self.d_k).transpose(1, 2)

        # Scaled dot-product attention
        scores = torch.matmul(Q, K.transpose(-2, -1)) / np.sqrt(self.d_k)

        if mask is not None:
            scores = scores.masked_fill(mask == 0, -1e9)

        attn = F.softmax(scores, dim=-1)
        attn = self.dropout(attn)

        # Apply attention to values
        context = torch.matmul(attn, V)

        # Reshape and project
        context = context.transpose(1, 2).contiguous().view(batch_size, -1, self.d_model)
        output = self.W_o(context)

        # Residual connection and layer norm
        output = self.layer_norm(output + residual)

        if squeeze_output:
            output = output.squeeze(1)

        return output


class AdaptiveGating(nn.Module):
    """
    Adaptive gating mechanism based on market regime.

    Different regimes may benefit from different model branches:
    - Trending: Time series models (DLinear, TimesNet)
    - Mean-reverting: Tabular models (indicators)
    - Volatile: Transformer (captures uncertainty)
    - Stable: Balanced combination
    """

    def __init__(
        self,
        n_branches: int = 3,
        n_regimes: int = 4,
        embedding_dim: int = 256,
    ):
        """
        Args:
            n_branches: Number of model branches (DLinear, TimesNet, Tabular)
            n_regimes: Number of market regimes
            embedding_dim: Embedding dimension
        """
        super().__init__()

        self.n_branches = n_branches
        self.n_regimes = n_regimes

        # Learnable gating weights for each regime
        # [n_regimes, n_branches]
        self.regime_gates = nn.Parameter(torch.ones(n_regimes, n_branches) / n_branches)

        # Dynamic gating based on embeddings
        self.dynamic_gate = nn.Sequential(
            nn.Linear(embedding_dim * n_branches, embedding_dim),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(embedding_dim, n_branches),
            nn.Softmax(dim=-1),
        )

    def forward(
        self,
        embeddings: List[torch.Tensor],
        regime_probs: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Compute adaptive gating weights.

        Args:
            embeddings: List of [batch, embedding_dim] from each branch
            regime_probs: [batch, n_regimes] regime probabilities

        Returns:
            gates: [batch, n_branches] gating weights
            final_embedding: [batch, embedding_dim] gated combination
        """
        batch_size = embeddings[0].size(0)

        # Static gates based on regime
        # [batch, n_regimes] @ [n_regimes, n_branches] -> [batch, n_branches]
        static_gates = torch.matmul(regime_probs, self.regime_gates)

        # Dynamic gates based on embeddings
        concat_embeddings = torch.cat(embeddings, dim=-1)  # [batch, embedding_dim * n_branches]
        dynamic_gates = self.dynamic_gate(concat_embeddings)  # [batch, n_branches]

        # Combine static and dynamic gates
        gates = 0.5 * static_gates + 0.5 * dynamic_gates
        gates = F.softmax(gates, dim=-1)  # Ensure sum to 1

        # Stack embeddings and apply gates
        stacked_embeddings = torch.stack(embeddings, dim=1)  # [batch, n_branches, embedding_dim]
        gates_expanded = gates.unsqueeze(-1)  # [batch, n_branches, 1]

        # Weighted combination
        final_embedding = (stacked_embeddings * gates_expanded).sum(dim=1)  # [batch, embedding_dim]

        return gates, final_embedding


class MetaFeatureExtractor(nn.Module):
    """
    Extract meta-features from time series for regime detection.

    Meta-features:
    - Volatility (rolling std of returns)
    - Trend (rolling mean of returns)
    - Correlation (autocorrelation)
    """

    def __init__(self, seq_len: int = 96, window: int = 24):
        """
        Args:
            seq_len: Input sequence length
            window: Rolling window for meta-features
        """
        super().__init__()

        self.seq_len = seq_len
        self.window = window

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Extract meta-features from time series.

        Args:
            x: [batch, seq_len, features] time series input

        Returns:
            meta_features: [batch, 3] containing [volatility, trend, correlation]
        """
        batch_size = x.size(0)

        # Use close price (assume it's the first feature)
        prices = x[:, :, 0]  # [batch, seq_len]

        # Compute returns
        returns = (prices[:, 1:] - prices[:, :-1]) / (prices[:, :-1] + 1e-8)

        # 1. Volatility: std of recent returns
        volatility = returns[:, -self.window:].std(dim=1)

        # 2. Trend: mean of recent returns
        trend = returns[:, -self.window:].mean(dim=1)

        # 3. Autocorrelation: correlation with lagged returns
        if returns.size(1) > self.window:
            returns_recent = returns[:, -self.window:]
            returns_lagged = returns[:, -2*self.window:-self.window]

            # Pad if necessary
            if returns_lagged.size(1) < self.window:
                pad_size = self.window - returns_lagged.size(1)
                returns_lagged = F.pad(returns_lagged, (pad_size, 0), value=0)

            # Compute correlation
            mean_recent = returns_recent.mean(dim=1, keepdim=True)
            mean_lagged = returns_lagged.mean(dim=1, keepdim=True)

            numerator = ((returns_recent - mean_recent) * (returns_lagged - mean_lagged)).sum(dim=1)
            denominator = (
                (returns_recent - mean_recent).pow(2).sum(dim=1).sqrt() *
                (returns_lagged - mean_lagged).pow(2).sum(dim=1).sqrt()
            )

            correlation = numerator / (denominator + 1e-8)
        else:
            correlation = torch.zeros(batch_size, device=x.device)

        # Stack meta-features
        meta_features = torch.stack([volatility, trend, correlation], dim=1)

        return meta_features


class AdvancedFusionModule(nn.Module):
    """
    Advanced fusion module combining all components.

    Architecture:
    1. Extract meta-features from time series
    2. Detect market regime
    3. Cross-attention between branches
    4. Adaptive gating based on regime
    5. Final layer normalization
    """

    def __init__(
        self,
        # Branch dimensions
        timeseries_dim: int = 256,  # DLinear + TimesNet + Transformer
        tabular_dim: int = 128,     # FT-Transformer
        # Fusion config
        fusion_dim: int = 384,
        n_heads: int = 8,
        n_regimes: int = 4,
        dropout: float = 0.1,
        # Meta-features
        seq_len: int = 96,
        meta_window: int = 24,
    ):
        """
        Initialize fusion module.

        Args:
            timeseries_dim: Time series embedding dimension
            tabular_dim: Tabular embedding dimension
            fusion_dim: Final fusion dimension
            n_heads: Number of attention heads
            n_regimes: Number of market regimes
            dropout: Dropout probability
            seq_len: Sequence length for meta-features
            meta_window: Window for meta-feature computation
        """
        super().__init__()

        self.timeseries_dim = timeseries_dim
        self.tabular_dim = tabular_dim
        self.fusion_dim = fusion_dim

        # Meta-feature extraction
        self.meta_extractor = MetaFeatureExtractor(seq_len, meta_window)

        # Market regime detection
        self.regime_detector = MarketRegimeDetector(
            meta_feature_dim=3,
            hidden_dim=64,
        )

        # Project embeddings to same dimension
        self.timeseries_proj = nn.Linear(timeseries_dim, fusion_dim)
        self.tabular_proj = nn.Linear(tabular_dim, fusion_dim)

        # Cross-attention between branches
        self.cross_attn_ts_to_tab = CrossBranchAttention(fusion_dim, n_heads, dropout)
        self.cross_attn_tab_to_ts = CrossBranchAttention(fusion_dim, n_heads, dropout)

        # Adaptive gating
        self.adaptive_gate = AdaptiveGating(
            n_branches=2,  # Time series + Tabular
            n_regimes=n_regimes,
            embedding_dim=fusion_dim,
        )

        # Final fusion layers
        self.fusion_mlp = nn.Sequential(
            nn.Linear(fusion_dim, fusion_dim * 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(fusion_dim * 2, fusion_dim),
        )

        # Advanced layer normalization
        self.layer_norm1 = nn.LayerNorm(fusion_dim)
        self.layer_norm2 = nn.LayerNorm(fusion_dim)
        self.final_layer_norm = nn.LayerNorm(fusion_dim)

        self.dropout = nn.Dropout(dropout)

    def forward(
        self,
        timeseries_embedding: torch.Tensor,
        tabular_embedding: torch.Tensor,
        timeseries_input: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """
        Fuse time series and tabular embeddings.

        Args:
            timeseries_embedding: [batch, timeseries_dim] from TimeSeriesBackbone
            tabular_embedding: [batch, tabular_dim] from FTTransformer
            timeseries_input: [batch, seq_len, features] original time series (for meta-features)

        Returns:
            Dict containing:
                - fused_embedding: [batch, fusion_dim] final fused representation
                - regime_probs: [batch, n_regimes] market regime probabilities
                - gating_weights: [batch, 2] adaptive gating weights
                - meta_features: [batch, 3] extracted meta-features
                - attention_weights: Dict of cross-attention weights
        """
        batch_size = timeseries_embedding.size(0)

        # 1. Extract meta-features
        if timeseries_input is not None:
            meta_features = self.meta_extractor(timeseries_input)
        else:
            # If no input provided, use zeros (won't affect much)
            meta_features = torch.zeros(batch_size, 3, device=timeseries_embedding.device)

        # 2. Detect market regime
        regime_logits, regime_probs = self.regime_detector(meta_features)

        # 3. Project embeddings to same dimension
        ts_proj = self.timeseries_proj(timeseries_embedding)  # [batch, fusion_dim]
        tab_proj = self.tabular_proj(tabular_embedding)        # [batch, fusion_dim]

        ts_proj = self.layer_norm1(ts_proj)
        tab_proj = self.layer_norm1(tab_proj)

        # 4. Cross-attention between branches
        # Time series attends to tabular
        ts_attended = self.cross_attn_ts_to_tab(
            query=ts_proj,
            key=tab_proj,
            value=tab_proj,
        )

        # Tabular attends to time series
        tab_attended = self.cross_attn_tab_to_ts(
            query=tab_proj,
            key=ts_proj,
            value=ts_proj,
        )

        ts_attended = self.layer_norm2(ts_attended)
        tab_attended = self.layer_norm2(tab_attended)

        # 5. Adaptive gating based on regime
        embeddings = [ts_attended, tab_attended]
        gating_weights, gated_embedding = self.adaptive_gate(embeddings, regime_probs)

        # 6. Final fusion MLP with residual
        fused = self.fusion_mlp(gated_embedding)
        fused = self.dropout(fused)
        fused = self.final_layer_norm(fused + gated_embedding)

        return {
            "fused_embedding": fused,
            "regime_probs": regime_probs,
            "regime_logits": regime_logits,
            "gating_weights": gating_weights,
            "meta_features": meta_features,
            "ts_attended": ts_attended,
            "tab_attended": tab_attended,
        }


class FusionStrategy(nn.Module):
    """
    Different fusion strategies for experimentation.

    Strategies:
    - "concat": Simple concatenation
    - "weighted": Learnable weighted combination
    - "attention": Cross-attention fusion
    - "adaptive": Full adaptive fusion with regime detection
    """

    def __init__(
        self,
        strategy: str = "adaptive",
        timeseries_dim: int = 256,
        tabular_dim: int = 128,
        fusion_dim: int = 384,
        **kwargs,
    ):
        """
        Args:
            strategy: Fusion strategy name
            timeseries_dim: Time series embedding dimension
            tabular_dim: Tabular embedding dimension
            fusion_dim: Output fusion dimension
            **kwargs: Additional arguments for specific strategies
        """
        super().__init__()

        self.strategy = strategy

        if strategy == "concat":
            # Simple concatenation + projection
            self.fusion = nn.Sequential(
                nn.Linear(timeseries_dim + tabular_dim, fusion_dim),
                nn.LayerNorm(fusion_dim),
                nn.ReLU(),
                nn.Dropout(kwargs.get("dropout", 0.1)),
            )

        elif strategy == "weighted":
            # Learnable weights for each branch
            self.ts_weight = nn.Parameter(torch.tensor(0.5))
            self.tab_weight = nn.Parameter(torch.tensor(0.5))

            self.ts_proj = nn.Linear(timeseries_dim, fusion_dim)
            self.tab_proj = nn.Linear(tabular_dim, fusion_dim)

            self.layer_norm = nn.LayerNorm(fusion_dim)

        elif strategy == "attention":
            # Cross-attention based fusion
            assert timeseries_dim == tabular_dim, "For attention fusion, dims must match"

            self.cross_attn = CrossBranchAttention(
                d_model=timeseries_dim,
                n_heads=kwargs.get("n_heads", 8),
                dropout=kwargs.get("dropout", 0.1),
            )

            self.fusion = nn.Sequential(
                nn.Linear(timeseries_dim * 2, fusion_dim),
                nn.LayerNorm(fusion_dim),
            )

        elif strategy == "adaptive":
            # Full adaptive fusion module
            self.fusion = AdvancedFusionModule(
                timeseries_dim=timeseries_dim,
                tabular_dim=tabular_dim,
                fusion_dim=fusion_dim,
                **kwargs,
            )

        else:
            raise ValueError(f"Unknown fusion strategy: {strategy}")

    def forward(
        self,
        timeseries_embedding: torch.Tensor,
        tabular_embedding: torch.Tensor,
        timeseries_input: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """
        Apply fusion strategy.

        Args:
            timeseries_embedding: [batch, timeseries_dim]
            tabular_embedding: [batch, tabular_dim]
            timeseries_input: [batch, seq_len, features] (for adaptive strategy)

        Returns:
            Dict with at least "fused_embedding" key
        """
        if self.strategy == "concat":
            # Concatenate and project
            concat = torch.cat([timeseries_embedding, tabular_embedding], dim=-1)
            fused = self.fusion(concat)
            return {"fused_embedding": fused}

        elif self.strategy == "weighted":
            # Weighted combination
            ts_proj = self.ts_proj(timeseries_embedding)
            tab_proj = self.tab_proj(tabular_embedding)

            # Normalize weights
            weights = F.softmax(torch.stack([self.ts_weight, self.tab_weight]), dim=0)

            fused = weights[0] * ts_proj + weights[1] * tab_proj
            fused = self.layer_norm(fused)

            return {
                "fused_embedding": fused,
                "weights": weights,
            }

        elif self.strategy == "attention":
            # Cross-attention
            ts_to_tab = self.cross_attn(
                query=timeseries_embedding,
                key=tabular_embedding,
                value=tabular_embedding,
            )

            # Concatenate original and attended
            fused = self.fusion(torch.cat([timeseries_embedding, ts_to_tab], dim=-1))

            return {
                "fused_embedding": fused,
                "attended": ts_to_tab,
            }

        elif self.strategy == "adaptive":
            # Full adaptive fusion
            return self.fusion(
                timeseries_embedding,
                tabular_embedding,
                timeseries_input,
            )

        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")
