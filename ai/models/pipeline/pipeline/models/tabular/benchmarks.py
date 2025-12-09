"""
Benchmark FT-Transformer against TabNet and XGBoost.
"""
import time
from typing import Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    accuracy_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

from .ft_transformer import FTTransformer
from .tabnet import TabNetModel, HAS_TABNET


class TabularBenchmark:
    """
    Benchmark tabular models on given dataset.

    Compares:
    - FT-Transformer
    - TabNet (if installed)
    - XGBoost (if installed)
    """

    def __init__(
        self,
        task_type: str = "regression",
        random_state: int = 42,
        device: str = "cpu",
    ):
        """
        Initialize benchmark.

        Args:
            task_type: 'regression' or 'classification'
            random_state: Random seed
            device: Device for PyTorch models
        """
        self.task_type = task_type
        self.random_state = random_state
        self.device = device

        self.results = {}

    def prepare_data(
        self,
        X: np.ndarray,
        y: np.ndarray,
        test_size: float = 0.2,
        val_size: float = 0.1,
    ) -> Dict:
        """
        Split and prepare data.

        Args:
            X: Features [n_samples, n_features]
            y: Targets [n_samples]
            test_size: Test set proportion
            val_size: Validation set proportion

        Returns:
            Dict with train/val/test splits
        """
        # Train/test split
        X_temp, X_test, y_temp, y_test = train_test_split(
            X, y,
            test_size=test_size,
            random_state=self.random_state
        )

        # Train/val split
        val_ratio = val_size / (1 - test_size)
        X_train, X_val, y_train, y_val = train_test_split(
            X_temp, y_temp,
            test_size=val_ratio,
            random_state=self.random_state
        )

        return {
            "X_train": X_train,
            "y_train": y_train,
            "X_val": X_val,
            "y_val": y_val,
            "X_test": X_test,
            "y_test": y_test,
        }

    def train_ft_transformer(
        self,
        data: Dict,
        n_epochs: int = 100,
        batch_size: int = 256,
        lr: float = 1e-4,
        early_stopping_patience: int = 10,
        label_smoothing: float = 0.0,
        **model_kwargs,
    ) -> Tuple[FTTransformer, Dict]:
        """
        Train FT-Transformer.

        Args:
            data: Data dict from prepare_data()
            n_epochs: Number of epochs
            batch_size: Batch size
            lr: Learning rate
            early_stopping_patience: Patience for early stopping
            label_smoothing: Label smoothing factor (classification only)
            **model_kwargs: Additional model arguments

        Returns:
            model, metrics_dict
        """
        print("\n" + "="*60)
        print("Training FT-Transformer")
        print("="*60)

        X_train = torch.FloatTensor(data["X_train"]).to(self.device)
        y_train = torch.FloatTensor(data["y_train"]).to(self.device)
        X_val = torch.FloatTensor(data["X_val"]).to(self.device)
        y_val = torch.FloatTensor(data["y_val"]).to(self.device)
        X_test = torch.FloatTensor(data["X_test"]).to(self.device)
        y_test = torch.FloatTensor(data["y_test"]).to(self.device)

        n_features = X_train.shape[1]
        n_classes = None if self.task_type == "regression" else len(np.unique(data["y_train"]))

        # Model
        model = FTTransformer(
            n_features=n_features,
            n_classes=n_classes,
            **model_kwargs
        ).to(self.device)

        # Loss
        if self.task_type == "classification":
            if label_smoothing > 0:
                criterion = nn.CrossEntropyLoss(label_smoothing=label_smoothing)
            else:
                criterion = nn.CrossEntropyLoss()
            y_train = y_train.long()
            y_val = y_val.long()
            y_test = y_test.long()
        else:
            criterion = nn.MSELoss()
            y_train = y_train.unsqueeze(1)
            y_val = y_val.unsqueeze(1)
            y_test = y_test.unsqueeze(1)

        # Optimizer
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)

        # Training
        train_dataset = TensorDataset(X_train, y_train)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        best_val_loss = float('inf')
        patience_counter = 0

        start_time = time.time()

        for epoch in range(n_epochs):
            # Train
            model.train()
            train_loss = 0
            for batch_X, batch_y in train_loader:
                optimizer.zero_grad()
                output = model(batch_X)
                loss = criterion(output, batch_y)
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            train_loss /= len(train_loader)

            # Validate
            model.eval()
            with torch.no_grad():
                val_output = model(X_val)
                val_loss = criterion(val_output, y_val).item()

            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                best_model_state = model.state_dict()
            else:
                patience_counter += 1

            if patience_counter >= early_stopping_patience:
                print(f"Early stopping at epoch {epoch+1}")
                break

            if (epoch + 1) % 10 == 0:
                print(f"Epoch {epoch+1}/{n_epochs} - "
                      f"Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}")

        # Load best model
        model.load_state_dict(best_model_state)

        training_time = time.time() - start_time

        # Test
        model.eval()
        with torch.no_grad():
            test_output = model(X_test)

        # Metrics
        if self.task_type == "classification":
            y_pred = torch.argmax(test_output, dim=1).cpu().numpy()
            y_true = y_test.cpu().numpy().flatten()

            metrics = {
                "accuracy": accuracy_score(y_true, y_pred),
                "training_time": training_time,
                "n_params": sum(p.numel() for p in model.parameters()),
            }

            # ROC AUC if binary
            if n_classes == 2:
                y_prob = torch.softmax(test_output, dim=1)[:, 1].cpu().numpy()
                metrics["roc_auc"] = roc_auc_score(y_true, y_prob)
        else:
            y_pred = test_output.cpu().numpy().flatten()
            y_true = y_test.cpu().numpy().flatten()

            metrics = {
                "mae": mean_absolute_error(y_true, y_pred),
                "mse": mean_squared_error(y_true, y_pred),
                "rmse": np.sqrt(mean_squared_error(y_true, y_pred)),
                "r2": r2_score(y_true, y_pred),
                "training_time": training_time,
                "n_params": sum(p.numel() for p in model.parameters()),
            }

        print("\nFT-Transformer Results:")
        for key, value in metrics.items():
            print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

        return model, metrics

    def train_xgboost(self, data: Dict, **xgb_params) -> Tuple[Optional[object], Dict]:
        """Train XGBoost baseline."""
        if not HAS_XGB:
            print("\nXGBoost not installed. Skipping.")
            return None, {}

        print("\n" + "="*60)
        print("Training XGBoost")
        print("="*60)

        start_time = time.time()

        if self.task_type == "classification":
            model = xgb.XGBClassifier(
                random_state=self.random_state,
                **xgb_params
            )
        else:
            model = xgb.XGBRegressor(
                random_state=self.random_state,
                **xgb_params
            )

        model.fit(
            data["X_train"], data["y_train"],
            eval_set=[(data["X_val"], data["y_val"])],
            verbose=False
        )

        training_time = time.time() - start_time

        # Test
        y_pred = model.predict(data["X_test"])
        y_true = data["y_test"]

        # Metrics
        if self.task_type == "classification":
            metrics = {
                "accuracy": accuracy_score(y_true, y_pred),
                "training_time": training_time,
            }

            if len(np.unique(y_true)) == 2:
                y_prob = model.predict_proba(data["X_test"])[:, 1]
                metrics["roc_auc"] = roc_auc_score(y_true, y_prob)
        else:
            metrics = {
                "mae": mean_absolute_error(y_true, y_pred),
                "mse": mean_squared_error(y_true, y_pred),
                "rmse": np.sqrt(mean_squared_error(y_true, y_pred)),
                "r2": r2_score(y_true, y_pred),
                "training_time": training_time,
            }

        print("\nXGBoost Results:")
        for key, value in metrics.items():
            print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

        return model, metrics

    def run_benchmark(
        self,
        X: np.ndarray,
        y: np.ndarray,
        models_to_run: list = ["ft_transformer", "xgboost"],
        **kwargs,
    ) -> Dict:
        """
        Run full benchmark.

        Args:
            X: Features
            y: Targets
            models_to_run: List of models to benchmark
            **kwargs: Model-specific kwargs

        Returns:
            Dict with results for each model
        """
        print("\n" + "="*60)
        print("TABULAR MODEL BENCHMARK")
        print("="*60)
        print(f"Dataset: {X.shape[0]} samples, {X.shape[1]} features")
        print(f"Task: {self.task_type}")

        # Prepare data
        data = self.prepare_data(X, y)

        results = {}

        # FT-Transformer
        if "ft_transformer" in models_to_run:
            ft_model, ft_metrics = self.train_ft_transformer(
                data,
                **kwargs.get("ft_transformer", {})
            )
            results["ft_transformer"] = {
                "model": ft_model,
                "metrics": ft_metrics,
            }

        # XGBoost
        if "xgboost" in models_to_run and HAS_XGB:
            xgb_model, xgb_metrics = self.train_xgboost(
                data,
                **kwargs.get("xgboost", {})
            )
            results["xgboost"] = {
                "model": xgb_model,
                "metrics": xgb_metrics,
            }

        # TabNet
        if "tabnet" in models_to_run and HAS_TABNET:
            print("\nTabNet training not yet implemented in benchmark.")

        # Summary
        self._print_summary(results)

        return results

    def _print_summary(self, results: Dict):
        """Print benchmark summary."""
        print("\n" + "="*60)
        print("BENCHMARK SUMMARY")
        print("="*60)

        if not results:
            print("No results to display")
            return

        # Get metric names
        metric_names = list(next(iter(results.values()))["metrics"].keys())

        # Print table header
        print(f"{'Model':<20}", end="")
        for metric in metric_names:
            print(f"{metric:<15}", end="")
        print()
        print("-" * 60)

        # Print each model
        for model_name, result in results.items():
            print(f"{model_name:<20}", end="")
            for metric in metric_names:
                value = result["metrics"].get(metric, "N/A")
                if isinstance(value, float):
                    print(f"{value:<15.4f}", end="")
                else:
                    print(f"{str(value):<15}", end="")
            print()

        # Best model
        if self.task_type == "regression":
            best_metric = "rmse"
            best_model = min(
                results.items(),
                key=lambda x: x[1]["metrics"].get(best_metric, float('inf'))
            )
        else:
            best_metric = "accuracy"
            best_model = max(
                results.items(),
                key=lambda x: x[1]["metrics"].get(best_metric, 0)
            )

        print("\n" + "="*60)
        print(f"Best model: {best_model[0]} ({best_metric}: {best_model[1]['metrics'][best_metric]:.4f})")
        print("="*60)
