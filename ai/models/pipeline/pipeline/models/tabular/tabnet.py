"""
TabNet wrapper for PyTorch.

TabNet uses sequential attention for feature selection.
"""
import torch
import torch.nn as nn

try:
    from pytorch_tabnet.tab_model import TabNetRegressor, TabNetClassifier
    HAS_TABNET = True
except ImportError:
    HAS_TABNET = False


class TabNetModel(nn.Module):
    """
    Wrapper for TabNet model.

    TabNet uses sequential attention mechanism for interpretable feature selection.
    """

    def __init__(
        self,
        n_features: int,
        n_d: int = 64,
        n_a: int = 64,
        n_steps: int = 3,
        gamma: float = 1.3,
        n_independent: int = 2,
        n_shared: int = 2,
        embedding_dim: int = 128,
        n_classes: int = None,
        device: str = "cpu",
    ):
        """
        Initialize TabNet wrapper.

        Args:
            n_features: Number of input features
            n_d: Dimension of decision prediction layer
            n_a: Dimension of attention embedding
            n_steps: Number of sequential steps
            gamma: Relaxation factor for feature reusage
            n_independent: Number of independent GLU layers
            n_shared: Number of shared GLU layers
            embedding_dim: Output embedding dimension
            n_classes: Number of classes (None for regression)
            device: Device to use
        """
        super().__init__()

        if not HAS_TABNET:
            raise ImportError(
                "pytorch-tabnet not installed. "
                "Install with: pip install pytorch-tabnet"
            )

        self.n_features = n_features
        self.embedding_dim = embedding_dim
        self.n_classes = n_classes
        self.device = device

        # TabNet model
        if n_classes is not None:
            self.model = TabNetClassifier(
                n_d=n_d,
                n_a=n_a,
                n_steps=n_steps,
                gamma=gamma,
                n_independent=n_independent,
                n_shared=n_shared,
                device_name=device,
            )
        else:
            self.model = TabNetRegressor(
                n_d=n_d,
                n_a=n_a,
                n_steps=n_steps,
                gamma=gamma,
                n_independent=n_independent,
                n_shared=n_shared,
                device_name=device,
            )

        # Embedding projection
        self.embedding_proj = nn.Linear(n_d * n_steps, embedding_dim)

    def forward(self, x: torch.Tensor, return_embedding: bool = False):
        """
        Forward pass.

        Args:
            x: [batch, n_features]
            return_embedding: Return embedding instead of predictions

        Returns:
            predictions or embeddings
        """
        # TabNet forward
        # Note: TabNet has its own training API
        # This is simplified for inference
        output, M_loss = self.model.network(x)

        if return_embedding:
            # Project to embedding
            embedding = self.embedding_proj(output)
            return embedding

        return output

    def fit(self, X_train, y_train, X_val=None, y_val=None, **kwargs):
        """
        Fit TabNet model.

        Args:
            X_train: Training features
            y_train: Training targets
            X_val: Validation features
            y_val: Validation targets
            **kwargs: Additional arguments for TabNet fit
        """
        self.model.fit(
            X_train=X_train,
            y_train=y_train,
            eval_set=[(X_val, y_val)] if X_val is not None else None,
            **kwargs
        )

    def predict(self, X):
        """Predict with TabNet."""
        return self.model.predict(X)
