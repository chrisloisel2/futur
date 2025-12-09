"""
Contrastive learning losses and augmentations for time series.

Implements:
- TS2Vec loss (hierarchical contrasting)
- NT-Xent loss (SimCLR)
- Supervised contrastive loss
- Time series augmentations
"""
from typing import Callable, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class TS2VecLoss(nn.Module):
    """
    TS2Vec contrastive loss with hierarchical contrasting.

    Combines temporal and instance-wise contrasting to learn
    universal time series representations.

    Reference: "TS2Vec: Towards Universal Representation of Time Series"
    """

    def __init__(
        self,
        temperature: float = 0.2,
        temporal_unit: int = 0,
    ):
        """
        Args:
            temperature: Temperature parameter for softmax
            temporal_unit: Granularity for temporal contrasting (0 = finest)
        """
        super().__init__()
        self.temperature = temperature
        self.temporal_unit = temporal_unit

    def hierarchical_contrastive_loss(
        self,
        z1: torch.Tensor,
        z2: torch.Tensor,
        temporal_unit: int = 0,
    ) -> torch.Tensor:
        """
        Compute hierarchical contrastive loss.

        Args:
            z1: [batch, seq_len, dim] embeddings from view 1
            z2: [batch, seq_len, dim] embeddings from view 2
            temporal_unit: Temporal granularity

        Returns:
            loss: Scalar loss
        """
        batch_size, seq_len, dim = z1.shape

        # Pool embeddings at the specified temporal unit
        if temporal_unit == 0:
            # Finest granularity: use all timesteps
            z1_pooled = z1
            z2_pooled = z2
        else:
            # Coarser granularity: average over windows
            pool_size = 2 ** temporal_unit

            # Pad if necessary
            pad_len = (pool_size - seq_len % pool_size) % pool_size
            if pad_len > 0:
                z1 = F.pad(z1, (0, 0, 0, pad_len))
                z2 = F.pad(z2, (0, 0, 0, pad_len))
                seq_len = z1.shape[1]

            # Reshape and average
            z1_pooled = z1.reshape(batch_size, -1, pool_size, dim).mean(dim=2)
            z2_pooled = z2.reshape(batch_size, -1, pool_size, dim).mean(dim=2)

        # Flatten batch and time dimensions
        z1_flat = z1_pooled.reshape(-1, dim)  # [batch * seq_len, dim]
        z2_flat = z2_pooled.reshape(-1, dim)

        # Normalize
        z1_flat = F.normalize(z1_flat, dim=1)
        z2_flat = F.normalize(z2_flat, dim=1)

        # Compute similarity matrix
        sim_matrix = torch.matmul(z1_flat, z2_flat.T) / self.temperature

        # Create positive pairs mask
        n = z1_flat.shape[0]
        labels = torch.arange(n, device=z1.device)

        # Loss: InfoNCE
        loss = F.cross_entropy(sim_matrix, labels) + F.cross_entropy(sim_matrix.T, labels)
        loss = loss / 2

        return loss

    def forward(
        self,
        z1: torch.Tensor,
        z2: torch.Tensor,
    ) -> torch.Tensor:
        """
        Compute TS2Vec loss.

        Args:
            z1: [batch, seq_len, dim] embeddings from augmented view 1
            z2: [batch, seq_len, dim] embeddings from augmented view 2

        Returns:
            loss: Scalar loss
        """
        return self.hierarchical_contrastive_loss(z1, z2, self.temporal_unit)


class NTXentLoss(nn.Module):
    """
    NT-Xent (Normalized Temperature-scaled Cross Entropy) loss.

    Used in SimCLR and other contrastive learning frameworks.
    """

    def __init__(self, temperature: float = 0.5):
        """
        Args:
            temperature: Temperature parameter
        """
        super().__init__()
        self.temperature = temperature

    def forward(
        self,
        z1: torch.Tensor,
        z2: torch.Tensor,
    ) -> torch.Tensor:
        """
        Compute NT-Xent loss.

        Args:
            z1: [batch, dim] projections from view 1
            z2: [batch, dim] projections from view 2

        Returns:
            loss: Scalar loss
        """
        batch_size = z1.shape[0]

        # Normalize
        z1 = F.normalize(z1, dim=1)
        z2 = F.normalize(z2, dim=1)

        # Concatenate views
        z = torch.cat([z1, z2], dim=0)  # [2*batch, dim]

        # Compute similarity matrix
        sim_matrix = torch.matmul(z, z.T) / self.temperature  # [2*batch, 2*batch]

        # Create positive pairs mask
        mask = torch.eye(2 * batch_size, device=z.device, dtype=torch.bool)

        # Positive pairs: (i, i+batch) and (i+batch, i)
        pos_mask = torch.zeros_like(mask)
        pos_mask[:batch_size, batch_size:] = torch.eye(batch_size, device=z.device, dtype=torch.bool)
        pos_mask[batch_size:, :batch_size] = torch.eye(batch_size, device=z.device, dtype=torch.bool)

        # Mask out diagonal (self-similarity)
        sim_matrix = sim_matrix.masked_fill(mask, -1e9)

        # Compute loss
        exp_sim = torch.exp(sim_matrix)

        # Sum over negatives
        neg_sum = exp_sim.sum(dim=1, keepdim=True) - exp_sim.masked_fill(~mask, 0).sum(dim=1, keepdim=True)

        # Positive similarity
        pos_sim = sim_matrix.masked_fill(~pos_mask, -1e9)
        pos_sim = torch.logsumexp(pos_sim, dim=1)

        # Loss
        loss = -pos_sim + torch.log(neg_sum.squeeze(1))
        loss = loss.mean()

        return loss


class SupConLoss(nn.Module):
    """
    Supervised contrastive loss.

    Uses label information to define positive pairs.
    """

    def __init__(self, temperature: float = 0.5):
        """
        Args:
            temperature: Temperature parameter
        """
        super().__init__()
        self.temperature = temperature

    def forward(
        self,
        features: torch.Tensor,
        labels: torch.Tensor,
    ) -> torch.Tensor:
        """
        Compute supervised contrastive loss.

        Args:
            features: [batch, dim] feature representations
            labels: [batch] class labels

        Returns:
            loss: Scalar loss
        """
        batch_size = features.shape[0]

        # Normalize
        features = F.normalize(features, dim=1)

        # Compute similarity matrix
        sim_matrix = torch.matmul(features, features.T) / self.temperature

        # Create mask for positive pairs (same label)
        labels = labels.view(-1, 1)
        mask = torch.eq(labels, labels.T).float()

        # Mask out diagonal
        mask = mask - torch.eye(batch_size, device=features.device)

        # Compute log probabilities
        exp_sim = torch.exp(sim_matrix)
        log_prob = sim_matrix - torch.log(exp_sim.sum(dim=1, keepdim=True))

        # Compute mean of log-likelihood over positive pairs
        mean_log_prob_pos = (mask * log_prob).sum(1) / mask.sum(1)

        # Loss
        loss = -mean_log_prob_pos.mean()

        return loss


# ============================================================================
# Augmentations for time series
# ============================================================================


def jitter(x: torch.Tensor, sigma: float = 0.03) -> torch.Tensor:
    """Add Gaussian noise to time series."""
    noise = torch.randn_like(x) * sigma
    return x + noise


def scaling(x: torch.Tensor, sigma: float = 0.1) -> torch.Tensor:
    """Scale time series by random factor."""
    factor = torch.randn(x.shape[0], 1, 1, device=x.device) * sigma + 1.0
    return x * factor


def rotation(x: torch.Tensor) -> torch.Tensor:
    """Randomly flip time series."""
    flip = torch.rand(x.shape[0], 1, 1, device=x.device) > 0.5
    return torch.where(flip, -x, x)


def permutation(x: torch.Tensor, max_segments: int = 5) -> torch.Tensor:
    """Randomly permute segments of time series."""
    batch_size, seq_len, dim = x.shape

    # Random number of segments
    n_segments = torch.randint(2, max_segments + 1, (1,)).item()
    segment_len = seq_len // n_segments

    # Permute segments
    segments = []
    for i in range(n_segments):
        start = i * segment_len
        end = start + segment_len if i < n_segments - 1 else seq_len
        segments.append(x[:, start:end, :])

    # Random permutation
    perm = torch.randperm(n_segments)
    segments_permuted = [segments[i] for i in perm]

    return torch.cat(segments_permuted, dim=1)


def time_warp(x: torch.Tensor, sigma: float = 0.2) -> torch.Tensor:
    """Apply random time warping."""
    batch_size, seq_len, dim = x.shape

    # Generate smooth random warp
    warp = torch.cumsum(torch.randn(batch_size, seq_len, device=x.device) * sigma, dim=1)
    warp = warp - warp.mean(dim=1, keepdim=True)
    warp = warp / (warp.std(dim=1, keepdim=True) + 1e-8)

    # Apply warp via interpolation
    indices = torch.arange(seq_len, device=x.device, dtype=torch.float32)
    indices = indices.unsqueeze(0).expand(batch_size, -1)
    warped_indices = indices + warp
    warped_indices = warped_indices.clamp(0, seq_len - 1)

    # Linear interpolation
    x_warped = []
    for b in range(batch_size):
        idx = warped_indices[b]
        idx_floor = idx.long()
        idx_ceil = (idx_floor + 1).clamp(max=seq_len - 1)
        weight = (idx - idx_floor.float()).unsqueeze(1)

        x_interp = x[b, idx_floor] * (1 - weight) + x[b, idx_ceil] * weight
        x_warped.append(x_interp)

    return torch.stack(x_warped, dim=0)


def window_slice(x: torch.Tensor, reduce_ratio: float = 0.9) -> torch.Tensor:
    """Extract random window from time series."""
    batch_size, seq_len, dim = x.shape
    target_len = max(int(seq_len * reduce_ratio), 1)

    if target_len >= seq_len:
        return x

    starts = torch.randint(0, seq_len - target_len + 1, (batch_size,))

    slices = []
    for b in range(batch_size):
        start = starts[b]
        slices.append(x[b, start:start + target_len, :])

    return torch.stack(slices, dim=0)


def create_augmentations(
    augmentation_list: Optional[List[str]] = None,
    **kwargs,
) -> Callable:
    """
    Create augmentation pipeline.

    Args:
        augmentation_list: List of augmentation names
        **kwargs: Augmentation-specific parameters

    Returns:
        Augmentation function
    """
    if augmentation_list is None:
        augmentation_list = ['jitter', 'scaling']

    def augment(x: torch.Tensor) -> torch.Tensor:
        for aug_name in augmentation_list:
            if aug_name == 'jitter':
                sigma = kwargs.get('jitter_sigma', 0.03)
                x = jitter(x, sigma)
            elif aug_name == 'scaling':
                sigma = kwargs.get('scaling_sigma', 0.1)
                x = scaling(x, sigma)
            elif aug_name == 'rotation':
                x = rotation(x)
            elif aug_name == 'permutation':
                max_seg = kwargs.get('permutation_max_segments', 5)
                x = permutation(x, max_seg)
            elif aug_name == 'time_warp':
                sigma = kwargs.get('warp_sigma', 0.2)
                x = time_warp(x, sigma)
            elif aug_name == 'window_slice':
                ratio = kwargs.get('slice_ratio', 0.9)
                x = window_slice(x, ratio)

        return x

    return augment


def temporal_augmentation(
    x: torch.Tensor,
    augmentation_prob: float = 0.5,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Apply random augmentations to create two views.

    Args:
        x: [batch, seq_len, dim] input time series
        augmentation_prob: Probability of applying each augmentation

    Returns:
        x1: [batch, seq_len, dim] augmented view 1
        x2: [batch, seq_len, dim] augmented view 2
    """
    augmentations = ['jitter', 'scaling', 'permutation', 'time_warp']

    # Create two different augmented views
    x1 = x.clone()
    x2 = x.clone()

    for aug_fn in [jitter, scaling, rotation]:
        if torch.rand(1).item() < augmentation_prob:
            x1 = aug_fn(x1)
        if torch.rand(1).item() < augmentation_prob:
            x2 = aug_fn(x2)

    return x1, x2
