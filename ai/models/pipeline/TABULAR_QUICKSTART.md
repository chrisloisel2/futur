# 🚀 Tabular Models - Quick Start Guide

Guide rapide pour utiliser les modèles tabulaires.

---

## Installation

```bash
pip install torch>=2.0.0 pytorch-lightning>=2.0.0
pip install xgboost>=2.0.0          # For baseline
pip install pytorch-tabnet>=4.0     # Optional: For TabNet
```

---

## Usage en 3 étapes

### 1. Import

```python
from models.tabular import FTTransformer, TabularBenchmark
import numpy as np
```

### 2. Prepare data

```python
# Your tabular data
X = np.random.randn(1000, 20)  # [samples, features]
y = np.random.randint(0, 2, 1000)  # Binary classification

# Or from real crypto features
from pipeline import CcxtDataSource, build_feature_set, AdvancedPreprocessor

source = CcxtDataSource()
ohlcv = source.fetch_historical_range("BTC/USDT", "1h", "2024-01-01", "2024-02-01")
df = ohlcv_to_df(ohlcv)
features = build_feature_set(df)
features["target"] = (features["close"].shift(-4) > features["close"]).astype(int)
features = features.dropna()

preprocessor = AdvancedPreprocessor(target_col="target")
processed = preprocessor.fit_transform(features)

X = processed.drop("target", axis=1).values
y = processed["target"].values
```

### 3. Benchmark

```python
# Compare FT-Transformer vs XGBoost
benchmark = TabularBenchmark(
    task_type="classification",  # or "regression"
    random_state=42,
)

results = benchmark.run_benchmark(
    X, y,
    models_to_run=["ft_transformer", "xgboost"],
    ft_transformer={
        "n_epochs": 50,
        "batch_size": 256,
        "lr": 1e-3,
        "d_token": 64,
        "n_blocks": 3,
        "dropout": 0.1,
    },
)

# Output:
# ============================================================
# BENCHMARK SUMMARY
# ============================================================
# Model               accuracy       roc_auc        training_time
# ------------------------------------------------------------
# ft_transformer      0.6573         0.7124         18.4521
# xgboost             0.6234         0.6789         2.1234
#
# Best model: ft_transformer (accuracy: 0.6573)
```

---

## Advanced Usage

### Manual FT-Transformer Training

```python
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from models.tabular import FTTransformer

# Model
model = FTTransformer(
    n_features=20,
    n_classes=2,        # Classification
    d_token=64,         # Token dimension
    n_blocks=3,         # Transformer blocks
    attention_n_heads=8,
    embedding_dim=128,
)

# Data
X_train = torch.FloatTensor(X)
y_train = torch.LongTensor(y)
train_dataset = TensorDataset(X_train, y_train)
train_loader = DataLoader(train_dataset, batch_size=256, shuffle=True)

# Training
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
criterion = nn.CrossEntropyLoss(label_smoothing=0.1)

for epoch in range(50):
    model.train()
    total_loss = 0

    for batch_X, batch_y in train_loader:
        optimizer.zero_grad()
        logits = model(batch_X)
        loss = criterion(logits, batch_y)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()

    print(f"Epoch {epoch+1}: Loss = {total_loss/len(train_loader):.4f}")

# Inference
model.eval()
with torch.no_grad():
    X_test = torch.FloatTensor(X_test)

    # Get predictions
    logits = model(X_test)
    predictions = torch.argmax(logits, dim=1)

    # Get embeddings
    embeddings = model(X_test, return_embedding=True)  # [batch, 128]
```

### Regression

```python
model = FTTransformer(
    n_features=20,
    n_classes=None,  # None for regression
    d_token=64,
    n_blocks=3,
)

criterion = nn.MSELoss()

# Training loop same as above
# Output is [batch, 1] for regression
```

### Extract Embeddings

```python
model.eval()
with torch.no_grad():
    embeddings = model(X, return_embedding=True)  # [batch, 128]

# Use embeddings for:
# - Clustering
# - Visualization (t-SNE, UMAP)
# - Transfer learning
# - Similarity search
```

---

## Hyperparameter Guidelines

### Small Dataset (<5K samples)

```python
model = FTTransformer(
    n_features=20,
    n_classes=2,
    d_token=32,         # Small token dim
    n_blocks=2,         # Few blocks
    attention_n_heads=4,
    ffn_d_hidden=64,
    dropout=0.3,        # High dropout
    embedding_dim=64,
)

# Training
lr=1e-3
batch_size=128
label_smoothing=0.15  # Heavy regularization
```

### Medium Dataset (5K-50K samples)

```python
model = FTTransformer(
    n_features=20,
    n_classes=2,
    d_token=64,
    n_blocks=3,
    attention_n_heads=8,
    ffn_d_hidden=128,
    dropout=0.15,
    embedding_dim=128,
)

# Training
lr=5e-4
batch_size=256
label_smoothing=0.1
```

### Large Dataset (>50K samples)

```python
model = FTTransformer(
    n_features=20,
    n_classes=2,
    d_token=128,
    n_blocks=4,
    attention_n_heads=8,
    ffn_d_hidden=256,
    dropout=0.1,
    embedding_dim=256,
)

# Training
lr=1e-4
batch_size=512
label_smoothing=0.05
```

---

## Common Tasks

### 1. Price Direction Prediction

```python
from pipeline import build_feature_set

# Create target: up/down in next N hours
features["target_4h"] = (features["close"].shift(-4) > features["close"]).astype(int)

X = features.drop("target_4h", axis=1).values
y = features["target_4h"].values

benchmark = TabularBenchmark(task_type="classification")
results = benchmark.run_benchmark(X, y)
```

### 2. Returns Prediction (Regression)

```python
# Target: log returns
features["log_return"] = np.log(features["close"] / features["close"].shift(1))
features["target"] = features["log_return"].shift(-4)  # Predict 4h ahead

X = features.drop(["log_return", "target"], axis=1).values
y = features["target"].values

benchmark = TabularBenchmark(task_type="regression")
results = benchmark.run_benchmark(X, y)
```

### 3. Volatility Prediction

```python
# Target: realized volatility
features["returns"] = features["close"].pct_change()
features["target_vol"] = features["returns"].rolling(24).std().shift(-24)

X = features.drop(["returns", "target_vol"], axis=1).values
y = features["target_vol"].values

benchmark = TabularBenchmark(task_type="regression")
results = benchmark.run_benchmark(X, y)
```

---

## Comparison with XGBoost

| Aspect | FT-Transformer | XGBoost |
|--------|----------------|---------|
| **Training Speed** | Slower (minutes) | Faster (seconds) |
| **Inference Speed** | Fast (GPU) | Very fast |
| **Feature Interactions** | Learned attention | Tree splits |
| **Embeddings** | ✅ Yes (128D) | ❌ No |
| **GPU Support** | ✅ Yes | ⚠️ Limited |
| **Interpretability** | Attention weights | Feature importance |
| **Small Data** | ⚠️ Overfits | ✅ Robust |
| **Large Data** | ✅ Excellent | ✅ Good |
| **Hyperparameters** | More tuning needed | Few to tune |

**When to use FT-Transformer**:
- Medium/large datasets (>5K samples)
- Complex feature interactions
- Need embeddings for downstream tasks
- GPU available
- Transfer learning scenarios

**When to use XGBoost**:
- Small datasets (<5K samples)
- Need fast training/inference
- CPU-only environment
- Tree-friendly patterns
- Quick baseline

---

## Troubleshooting

### Issue: Model overfitting

**Solution**: Increase regularization
```python
model = FTTransformer(
    ...,
    dropout=0.3,              # Increase dropout
    attention_dropout=0.3,
    ffn_dropout=0.2,
)

# Use label smoothing
criterion = nn.CrossEntropyLoss(label_smoothing=0.15)
```

### Issue: Training too slow

**Solution**: Reduce model size or use GPU
```python
model = FTTransformer(
    ...,
    d_token=32,      # Smaller tokens
    n_blocks=2,      # Fewer blocks
    ffn_d_hidden=64, # Smaller FFN
)

# Or use GPU
model = model.to("cuda")
```

### Issue: Poor performance

**Solution**: More capacity or better preprocessing
```python
# 1. Increase model capacity
model = FTTransformer(
    ...,
    d_token=128,
    n_blocks=4,
    attention_n_heads=8,
)

# 2. Better preprocessing
from pipeline import AdvancedPreprocessor

preprocessor = AdvancedPreprocessor(
    target_col="target",
    d=0.5,                    # Fractional differentiation
    normalize_lookback=30,    # Rolling normalization
    feature_selection=True,   # Remove noise
)
processed = preprocessor.fit_transform(features)
```

### Issue: Out of memory

**Solution**: Reduce batch size or model size
```python
# Smaller batch size
batch_size = 64  # Instead of 256

# Smaller model
model = FTTransformer(
    ...,
    d_token=32,
    n_blocks=2,
)
```

---

## Next Steps

1. **Read full docs**: [TABULAR.md](TABULAR.md)
2. **Run examples**: `python example_tabular_benchmark.py`
3. **Experiment**: Try different hyperparameters
4. **Compare**: Benchmark against your existing models

---

**Version**: 2.3.0
**Module**: `models/tabular/`
**Full Documentation**: [TABULAR.md](TABULAR.md)
