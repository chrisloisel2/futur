"""
Tiny Recursive Model (TRM) for trading.

Architecture:
- Embedding layer: features → latent_dim
- Initial state computation
- Recursive reasoning block (GRU with shared weights)
- Output head: latent → prediction

Design principles:
- Minimal parameters (~10-50K)
- Shared weights across iterations
- Iterative refinement of understanding
"""
import logging
from typing import Optional, Tuple

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


class TinyRecursiveModel(nn.Module):
    """
    Tiny Recursive Model for trading prediction.

    The model performs iterative reasoning:
    1. Embed input features
    2. Initialize latent state
    3. Refine state through T iterations (shared weights)
    4. Predict from final state
    """

    def __init__(
        self,
        num_features: int,
        latent_dim: int = 32,
        hidden_dim: int = 64,
        num_iterations: int = 5,
        dropout: float = 0.1,
        output_mode: str = 'return'  # 'return' or 'classification'
    ):
        """
        Args:
            num_features: Number of input features per timestep
            latent_dim: Dimension of latent state (keep small!)
            hidden_dim: Hidden dimension in recursive block
            num_iterations: Number of recursive reasoning iterations
            dropout: Dropout probability
            output_mode: 'return' for regression, 'classification' for direction
        """
        super().__init__()

        self.num_features = num_features
        self.latent_dim = latent_dim
        self.hidden_dim = hidden_dim
        self.num_iterations = num_iterations
        self.output_mode = output_mode

        # 1. Feature embedding
        # Maps: [batch, seq_len, num_features] → [batch, seq_len, latent_dim]
        self.feature_embedding = nn.Sequential(
            nn.Linear(num_features, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, latent_dim),
            nn.LayerNorm(latent_dim)
        )

        # 2. Temporal aggregation (simple attention over sequence)
        # Reduces: [batch, seq_len, latent_dim] → [batch, latent_dim]
        self.temporal_attention = nn.Sequential(
            nn.Linear(latent_dim, 1),
            nn.Softmax(dim=1)
        )

        # 3. Recursive reasoning cell (GRU)
        # State evolves: h_t → h_{t+1}
        # Input: current state h_t + context (embedded features)
        self.reasoning_cell = nn.GRUCell(
            input_size=latent_dim,
            hidden_size=latent_dim
        )

        # 4. Output head
        if output_mode == 'return':
            # Predict continuous return value
            # CRITICAL: Add tanh() and scale based on target normalization
            self.output_head = nn.Sequential(
                nn.Linear(latent_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, 1),
                nn.Tanh()  # Output in [-1, 1]
            )
            # CRITICAL: Scale output to match normalized target range
            # Targets are normalized to mean=0, std=1
            # tanh gives ±1, we scale CONSERVATIVELY to ±0.5 to avoid gradient explosion in early training
            # Model can saturate gradually as it learns
            self.output_scale = 0.5
            logger.info(f"Output scale set to {self.output_scale:.2f} (normalized targets)")
        elif output_mode == 'classification':
            # Predict direction (up/down/neutral)
            self.output_head = nn.Sequential(
                nn.Linear(latent_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, 3)  # 3 classes: down, neutral, up
            )
        else:
            raise ValueError(f"Unknown output_mode: {output_mode}")

        # Initialize weights
        self._init_weights()

        # Log model size
        total_params = sum(p.numel() for p in self.parameters())
        trainable_params = sum(p.numel() for p in self.parameters() if p.requires_grad)
        logger.info(
            f"Initialized TRM: {trainable_params:,} trainable params "
            f"(latent_dim={latent_dim}, iterations={num_iterations})"
        )

    def _init_weights(self):
        """
        Initialize weights with STRICT stability constraints.
        - Xavier uniform for Linear layers
        - Orthogonal for GRU weights (prevents gradient explosion)
        - Zero bias everywhere
        """
        for name, param in self.named_parameters():
            # GRU weights: use orthogonal initialization
            if 'reasoning_cell' in name and 'weight' in name:
                if 'weight_hh' in name or 'weight_ih' in name:
                    nn.init.orthogonal_(param)
                else:
                    nn.init.xavier_uniform_(param, gain=0.5)
            # Linear weights: Xavier uniform
            elif 'weight' in name:
                if len(param.shape) >= 2:
                    nn.init.xavier_uniform_(param, gain=0.5)
                else:
                    nn.init.normal_(param, mean=0, std=0.01)
            # All biases: zero
            elif 'bias' in name:
                nn.init.zeros_(param)

    def embed_sequence(self, x: torch.Tensor) -> torch.Tensor:
        """
        Embed input sequence.

        Args:
            x: [batch, seq_len, num_features]

        Returns:
            embedded: [batch, seq_len, latent_dim]
        """
        batch_size, seq_len, _ = x.shape

        # Embed each timestep
        embedded = self.feature_embedding(x)  # [batch, seq_len, latent_dim]

        return embedded

    def aggregate_temporal_context(self, embedded: torch.Tensor) -> torch.Tensor:
        """
        Aggregate temporal sequence into a fixed context vector.

        Uses attention to weight different timesteps.

        Args:
            embedded: [batch, seq_len, latent_dim]

        Returns:
            context: [batch, latent_dim]
        """
        # Compute attention weights
        attention_scores = self.temporal_attention(embedded)  # [batch, seq_len, 1]

        # Weighted sum
        context = (embedded * attention_scores).sum(dim=1)  # [batch, latent_dim]

        return context

    def recursive_reasoning(
        self,
        initial_state: torch.Tensor,
        context: torch.Tensor
    ) -> Tuple[torch.Tensor, list[torch.Tensor]]:
        """
        Perform recursive reasoning iterations with STRICT activation clamping.

        Each iteration refines the latent state using the GRU cell with shared weights.
        CRITICAL: Clamp activations to [-1, 1] to prevent explosion.

        Args:
            initial_state: [batch, latent_dim] - initial hidden state
            context: [batch, latent_dim] - fixed context from input sequence

        Returns:
            final_state: [batch, latent_dim]
            state_history: List of states at each iteration (for analysis)
        """
        h = initial_state.clamp(-1.0, 1.0)
        state_history = [h]

        for t in range(self.num_iterations):
            # Update state: h_{t+1} = GRU(h_t, context)
            h = self.reasoning_cell(context, h)
            # CRITICAL: Clamp hidden state to prevent internal amplification
            h = h.clamp(-1.0, 1.0)
            state_history.append(h)

        return h, state_history

    def forward(
        self,
        x: torch.Tensor,
        return_states: bool = False
    ) -> torch.Tensor | Tuple[torch.Tensor, list[torch.Tensor]]:
        """
        Forward pass.

        Args:
            x: [batch, seq_len, num_features]
            return_states: If True, also return intermediate states (for analysis)

        Returns:
            output: [batch] or [batch, num_classes]
            states (optional): List of hidden states through iterations
        """
        # 1. Embed features
        embedded = self.embed_sequence(x)  # [batch, seq_len, latent_dim]

        # 2. Aggregate into context
        context = self.aggregate_temporal_context(embedded)  # [batch, latent_dim]

        # 3. Initialize state (use context as initial state)
        initial_state = context

        # 4. Recursive reasoning
        final_state, state_history = self.recursive_reasoning(initial_state, context)

        # 5. Output prediction
        output = self.output_head(final_state)  # [batch, 1] or [batch, 3]

        if self.output_mode == 'return':
            output = output.squeeze(-1)  # [batch]
            # CRITICAL: Scale tanh output from [-1,1] to match normalized target range
            output = output * self.output_scale
            # SAFETY: Clamp to ±5 in normalized space (extreme outliers only)
            max_output = 5.0
            output = output.clamp(-max_output, max_output)

        if return_states:
            return output, state_history
        else:
            return output

    def predict_direction_and_confidence(
        self,
        x: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Predict trading direction and confidence.

        Args:
            x: [batch, seq_len, num_features]

        Returns:
            direction: [batch] tensor with values {-1, 0, 1}
            confidence: [batch] tensor with values in [0, 1]
        """
        if self.output_mode == 'classification':
            logits = self.forward(x)  # [batch, 3]
            probs = torch.softmax(logits, dim=-1)

            # Direction: 0=down, 1=neutral, 2=up → map to {-1, 0, 1}
            direction = torch.argmax(probs, dim=-1) - 1  # [batch]

            # Confidence: max probability
            confidence = torch.max(probs, dim=-1)[0]  # [batch]

        else:  # return mode
            pred_return = self.forward(x)  # [batch]

            # Direction from sign of predicted return
            direction = torch.sign(pred_return)  # [batch]

            # Confidence from magnitude (normalized)
            confidence = torch.tanh(torch.abs(pred_return) * 10)  # [batch] in [0, 1]

        return direction, confidence


class TRMEnsemble(nn.Module):
    """
    Ensemble of multiple TRM models for robustness.

    Averages predictions from multiple independently initialized models.
    """

    def __init__(
        self,
        num_models: int,
        num_features: int,
        **trm_kwargs
    ):
        """
        Args:
            num_models: Number of TRM models in ensemble
            num_features: Number of input features
            **trm_kwargs: Arguments passed to TinyRecursiveModel
        """
        super().__init__()

        self.num_models = num_models
        self.models = nn.ModuleList([
            TinyRecursiveModel(num_features=num_features, **trm_kwargs)
            for _ in range(num_models)
        ])

        total_params = sum(p.numel() for p in self.parameters())
        logger.info(f"Initialized TRM Ensemble: {num_models} models, {total_params:,} total params")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass through ensemble (average predictions).

        Args:
            x: [batch, seq_len, num_features]

        Returns:
            output: [batch] - averaged predictions
        """
        outputs = [model(x) for model in self.models]
        return torch.stack(outputs).mean(dim=0)

    def predict_direction_and_confidence(
        self,
        x: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Ensemble prediction with voting.

        Args:
            x: [batch, seq_len, num_features]

        Returns:
            direction: [batch] - majority vote
            confidence: [batch] - agreement ratio
        """
        directions = []
        confidences = []

        for model in self.models:
            dir_i, conf_i = model.predict_direction_and_confidence(x)
            directions.append(dir_i)
            confidences.append(conf_i)

        directions = torch.stack(directions)  # [num_models, batch]
        confidences = torch.stack(confidences)  # [num_models, batch]

        # Majority vote for direction
        direction = torch.sign(directions.sum(dim=0))  # [batch]

        # Confidence as average
        confidence = confidences.mean(dim=0)  # [batch]

        return direction, confidence


if __name__ == "__main__":
    # Test TRM
    logging.basicConfig(level=logging.INFO)

    batch_size = 16
    seq_len = 60
    num_features = 10

    # Create random input
    x = torch.randn(batch_size, seq_len, num_features)

    # Test single model
    print("\nTesting TinyRecursiveModel:")
    model = TinyRecursiveModel(
        num_features=num_features,
        latent_dim=32,
        hidden_dim=64,
        num_iterations=5,
        output_mode='return'
    )

    output = model(x)
    print(f"Output shape: {output.shape}")
    print(f"Output sample: {output[:3]}")

    # Test with state history
    output, states = model(x, return_states=True)
    print(f"Number of states: {len(states)}")
    print(f"State shape: {states[0].shape}")

    # Test direction prediction
    direction, confidence = model.predict_direction_and_confidence(x)
    print(f"Direction: {direction[:5]}")
    print(f"Confidence: {confidence[:5]}")

    # Test ensemble
    print("\nTesting TRMEnsemble:")
    ensemble = TRMEnsemble(
        num_models=3,
        num_features=num_features,
        latent_dim=32,
        num_iterations=5
    )

    output = ensemble(x)
    print(f"Ensemble output shape: {output.shape}")

    direction, confidence = ensemble.predict_direction_and_confidence(x)
    print(f"Ensemble direction: {direction[:5]}")
    print(f"Ensemble confidence: {confidence[:5]}")

    print("\nTRM test passed!")
