"""
Masking strategies for self-supervised learning on time series.

Implements various masking patterns:
- Random masking
- Block masking (consecutive timesteps)
- Geometric masking (random length blocks)
"""
from typing import Tuple

import torch
import numpy as np


class RandomMasking:
    """
    Random masking strategy.

    Randomly masks tokens with probability `mask_ratio`.
    """

    def __init__(self, mask_ratio: float = 0.75):
        """
        Args:
            mask_ratio: Probability of masking each token
        """
        self.mask_ratio = mask_ratio

    def __call__(
        self,
        batch_size: int,
        seq_len: int,
        device: torch.device = None,
    ) -> torch.Tensor:
        """
        Generate random mask.

        Args:
            batch_size: Batch size
            seq_len: Sequence length
            device: Device to create mask on

        Returns:
            mask: [batch_size, seq_len] binary mask (1=keep, 0=mask)
        """
        if device is None:
            device = torch.device('cpu')

        mask = torch.rand(batch_size, seq_len, device=device) > self.mask_ratio
        return mask


class BlockMasking:
    """
    Block masking strategy.

    Masks contiguous blocks of tokens.
    More challenging than random masking as the model must
    predict longer sequences.
    """

    def __init__(
        self,
        mask_ratio: float = 0.75,
        block_length: int = 10,
    ):
        """
        Args:
            mask_ratio: Target ratio of masked tokens
            block_length: Length of each masked block
        """
        self.mask_ratio = mask_ratio
        self.block_length = block_length

    def __call__(
        self,
        batch_size: int,
        seq_len: int,
        device: torch.device = None,
    ) -> torch.Tensor:
        """
        Generate block mask.

        Args:
            batch_size: Batch size
            seq_len: Sequence length
            device: Device to create mask on

        Returns:
            mask: [batch_size, seq_len] binary mask (1=keep, 0=mask)
        """
        if device is None:
            device = torch.device('cpu')

        mask = torch.ones(batch_size, seq_len, device=device, dtype=torch.bool)

        # Calculate number of blocks to mask
        n_masked_tokens = int(seq_len * self.mask_ratio)
        n_blocks = max(1, n_masked_tokens // self.block_length)

        for b in range(batch_size):
            # Randomly select block start positions
            block_starts = torch.randperm(seq_len - self.block_length + 1)[:n_blocks]

            for start in block_starts:
                end = min(start + self.block_length, seq_len)
                mask[b, start:end] = False

        return mask


class GeometricMasking:
    """
    Geometric masking strategy.

    Masks blocks with lengths drawn from a geometric distribution.
    Allows for variable-length masked regions.
    """

    def __init__(
        self,
        mask_ratio: float = 0.75,
        mean_block_length: int = 10,
    ):
        """
        Args:
            mask_ratio: Target ratio of masked tokens
            mean_block_length: Mean length of masked blocks
        """
        self.mask_ratio = mask_ratio
        self.mean_block_length = mean_block_length
        # Geometric distribution parameter
        self.p = 1.0 / mean_block_length

    def __call__(
        self,
        batch_size: int,
        seq_len: int,
        device: torch.device = None,
    ) -> torch.Tensor:
        """
        Generate geometric mask.

        Args:
            batch_size: Batch size
            seq_len: Sequence length
            device: Device to create mask on

        Returns:
            mask: [batch_size, seq_len] binary mask (1=keep, 0=mask)
        """
        if device is None:
            device = torch.device('cpu')

        mask = torch.ones(batch_size, seq_len, device=device, dtype=torch.bool)

        for b in range(batch_size):
            n_masked = 0
            target_masked = int(seq_len * self.mask_ratio)

            while n_masked < target_masked:
                # Sample block length from geometric distribution
                block_length = np.random.geometric(self.p)
                block_length = min(block_length, target_masked - n_masked)

                # Sample start position
                if n_masked + block_length > seq_len:
                    break

                max_start = seq_len - block_length
                if max_start < 0:
                    break

                start = np.random.randint(0, max_start + 1)
                end = start + block_length

                # Mask block
                mask[b, start:end] = False
                n_masked += block_length

        return mask


class StructuredMasking:
    """
    Structured masking for time series with known periodicity.

    Masks entire periods or specific frequencies.
    Useful for learning seasonal patterns.
    """

    def __init__(
        self,
        period: int = 24,
        mask_ratio: float = 0.5,
    ):
        """
        Args:
            period: Period length (e.g., 24 for hourly data with daily periodicity)
            mask_ratio: Ratio of periods to mask
        """
        self.period = period
        self.mask_ratio = mask_ratio

    def __call__(
        self,
        batch_size: int,
        seq_len: int,
        device: torch.device = None,
    ) -> torch.Tensor:
        """
        Generate structured mask.

        Args:
            batch_size: Batch size
            seq_len: Sequence length
            device: Device to create mask on

        Returns:
            mask: [batch_size, seq_len] binary mask (1=keep, 0=mask)
        """
        if device is None:
            device = torch.device('cpu')

        mask = torch.ones(batch_size, seq_len, device=device, dtype=torch.bool)

        n_periods = seq_len // self.period
        n_masked_periods = max(1, int(n_periods * self.mask_ratio))

        for b in range(batch_size):
            # Randomly select periods to mask
            masked_periods = torch.randperm(n_periods)[:n_masked_periods]

            for period_idx in masked_periods:
                start = period_idx * self.period
                end = min(start + self.period, seq_len)
                mask[b, start:end] = False

        return mask


def get_masking_strategy(
    strategy: str,
    mask_ratio: float = 0.75,
    **kwargs,
):
    """
    Factory function to get masking strategy.

    Args:
        strategy: One of ['random', 'block', 'geometric', 'structured']
        mask_ratio: Masking ratio
        **kwargs: Additional strategy-specific arguments

    Returns:
        Masking strategy callable
    """
    if strategy == 'random':
        return RandomMasking(mask_ratio=mask_ratio)

    elif strategy == 'block':
        block_length = kwargs.get('block_length', 10)
        return BlockMasking(mask_ratio=mask_ratio, block_length=block_length)

    elif strategy == 'geometric':
        mean_block_length = kwargs.get('mean_block_length', 10)
        return GeometricMasking(mask_ratio=mask_ratio, mean_block_length=mean_block_length)

    elif strategy == 'structured':
        period = kwargs.get('period', 24)
        return StructuredMasking(period=period, mask_ratio=mask_ratio)

    else:
        raise ValueError(f"Unknown masking strategy: {strategy}")


def visualize_mask(mask: torch.Tensor, sample_idx: int = 0):
    """
    Visualize a mask (for debugging).

    Args:
        mask: [batch, seq_len] binary mask
        sample_idx: Which sample to visualize
    """
    try:
        import matplotlib.pyplot as plt

        mask_np = mask[sample_idx].cpu().numpy()

        plt.figure(figsize=(15, 3))
        plt.imshow(mask_np.reshape(1, -1), cmap='gray', aspect='auto')
        plt.title(f'Mask visualization (1=keep, 0=mask)')
        plt.xlabel('Time step')
        plt.yticks([])
        plt.colorbar()
        plt.tight_layout()
        plt.show()

    except ImportError:
        print("Matplotlib not available for visualization")
