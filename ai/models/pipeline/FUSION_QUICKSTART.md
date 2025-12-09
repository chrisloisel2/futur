# 🚀 Fusion - Quick Start

Guide rapide pour utiliser le module de fusion.

---

## Installation

```bash
# Already included if you installed pipeline
pip install torch>=2.0.0
```

---

## Usage en 3 étapes

### 1. Get embeddings from each branch

```python
from models import TimeSeriesBackbone
from models.tabular import FTTransformer
import torch

# Time series model
ts_model = TimeSeriesBackbone(seq_len=96, pred_len=24, enc_in=7, embedding_dim=256)

# Tabular model
tab_model = FTTransformer(n_features=20, n_classes=None, embedding_dim=128)

# Data
x_ts = torch.randn(32, 96, 7)    # [batch, seq_len, features]
x_tab = torch.randn(32, 20)      # [batch, features]

# Get embeddings
ts_emb = ts_model(x_ts)                                  # [32, 256]
tab_emb = tab_model(x_tab, return_embedding=True)        # [32, 128]
```

### 2. Choose fusion strategy

```python
from models.fusion import FusionStrategy

# Option A: Simple concat (baseline)
fusion = FusionStrategy(strategy="concat", timeseries_dim=256, tabular_dim=128, fusion_dim=384)

# Option B: Learnable weights
fusion = FusionStrategy(strategy="weighted", timeseries_dim=256, tabular_dim=128, fusion_dim=384)

# Option C: Cross-attention
fusion = FusionStrategy(strategy="attention", timeseries_dim=256, tabular_dim=256, fusion_dim=384)

# Option D: Adaptive (best performance)
fusion = FusionStrategy(
    strategy="adaptive",
    timeseries_dim=256,
    tabular_dim=128,
    fusion_dim=384,
    n_heads=8,
    n_regimes=4,
    seq_len=96,
)
```

### 3. Fuse and use

```python
# Fuse embeddings
outputs = fusion(ts_emb, tab_emb, x_ts if strategy == "adaptive" else None)

fused_embedding = outputs["fused_embedding"]  # [32, 384]

# Use for downstream task
import torch.nn as nn
classifier = nn.Linear(384, 2)
logits = classifier(fused_embedding)
```

---

## Complete Example

```python
from models import TimeSeriesBackbone
from models.tabular import FTTransformer
from models.fusion import AdvancedFusionModule
import torch
import torch.nn as nn

# 1. CREATE MODELS
ts_model = TimeSeriesBackbone(seq_len=96, pred_len=24, enc_in=7, embedding_dim=256)
tab_model = FTTransformer(n_features=20, n_classes=None, embedding_dim=128)

fusion = AdvancedFusionModule(
    timeseries_dim=256,
    tabular_dim=128,
    fusion_dim=384,
    n_heads=8,
    n_regimes=4,
)

classifier = nn.Linear(384, 2)

# 2. FORWARD PASS
x_ts = torch.randn(32, 96, 7)
x_tab = torch.randn(32, 20)
y = torch.randint(0, 2, (32,))

# Get embeddings
ts_emb = ts_model(x_ts)
tab_emb = tab_model(x_tab, return_embedding=True)

# Fuse
fusion_outputs = fusion(ts_emb, tab_emb, x_ts)
fused = fusion_outputs["fused_embedding"]

# Classify
logits = classifier(fused)

# 3. TRAIN
optimizer = torch.optim.AdamW(
    list(ts_model.parameters()) +
    list(tab_model.parameters()) +
    list(fusion.parameters()) +
    list(classifier.parameters()),
    lr=1e-4
)

criterion = nn.CrossEntropyLoss()

loss = criterion(logits, y)
loss.backward()
optimizer.step()

# 4. ANALYZE FUSION
print(f"Regime probabilities: {fusion_outputs['regime_probs'].mean(dim=0)}")
print(f"Gating weights: {fusion_outputs['gating_weights'].mean(dim=0)}")
print(f"Meta-features: {fusion_outputs['meta_features'].mean(dim=0)}")
```

---

## When to Use Which Strategy?

| Strategy | Use Case | Performance | Speed |
|----------|----------|-------------|-------|
| **Concat** | Quick baseline, small data | Good | Fast |
| **Weighted** | Learn branch importance | Better | Fast |
| **Attention** | Strong interaction needed | Better | Medium |
| **Adaptive** | Best results, market regimes | Best | Slower |

---

## Common Patterns

### Pattern 1: End-to-end training

```python
class FusedModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.ts_branch = TimeSeriesBackbone(...)
        self.tab_branch = FTTransformer(...)
        self.fusion = AdvancedFusionModule(...)
        self.head = nn.Linear(384, n_classes)

    def forward(self, x_ts, x_tab):
        ts_emb = self.ts_branch(x_ts)
        tab_emb = self.tab_branch(x_tab, return_embedding=True)
        fused = self.fusion(ts_emb, tab_emb, x_ts)["fused_embedding"]
        return self.head(fused)

model = FusedModel()
# Train end-to-end...
```

### Pattern 2: Frozen branches + fine-tune fusion

```python
# Load pre-trained branches
ts_model = TimeSeriesBackbone.load_pretrained("ts_model.pt")
tab_model = FTTransformer.load_pretrained("tab_model.pt")

# Freeze
for param in ts_model.parameters():
    param.requires_grad = False
for param in tab_model.parameters():
    param.requires_grad = False

# Only train fusion + classifier
fusion = AdvancedFusionModule(...)
classifier = nn.Linear(384, 2)

optimizer = torch.optim.AdamW(
    list(fusion.parameters()) + list(classifier.parameters()),
    lr=1e-3
)
```

### Pattern 3: Compare strategies

```python
strategies = ["concat", "weighted", "adaptive"]
results = {}

for strategy in strategies:
    model = FusedModel(fusion_strategy=strategy)
    # Train...
    acc = evaluate(model, test_loader)
    results[strategy] = acc

best = max(results, key=results.get)
print(f"Best strategy: {best} (acc={results[best]:.3f})")
```

---

## Debugging Tips

### Tip 1: Check regime distribution

```python
outputs = fusion(ts_emb, tab_emb, x_ts)
regime_probs = outputs["regime_probs"]

import matplotlib.pyplot as plt
plt.hist(regime_probs.argmax(dim=1).numpy(), bins=4)
plt.xticks([0, 1, 2, 3], ["Trending", "MeanRev", "Volatile", "Stable"])
plt.show()
```

### Tip 2: Visualize gating weights

```python
gates = outputs["gating_weights"].detach().numpy()

plt.scatter(gates[:, 0], gates[:, 1])
plt.xlabel("Time Series Weight")
plt.ylabel("Tabular Weight")
plt.title("Gating Distribution")
plt.show()
```

### Tip 3: Check meta-features

```python
meta = outputs["meta_features"].detach().numpy()

print(f"Average volatility: {meta[:, 0].mean():.3f}")
print(f"Average trend: {meta[:, 1].mean():.3f}")
print(f"Average correlation: {meta[:, 2].mean():.3f}")
```

---

## Troubleshooting

### Issue: Fusion doesn't improve over single branch

**Solution**: Check if branches are learning
```python
# Before fusion training
ts_acc = evaluate(ts_model, test_loader)
tab_acc = evaluate(tab_model, test_loader)

print(f"TS accuracy: {ts_acc:.3f}")
print(f"Tabular accuracy: {tab_acc:.3f}")

# If one branch is much worse, fusion won't help much
```

### Issue: Gating always favors one branch

**Solution**: Check if branches have different capacities
```python
# Make sure embeddings are similar scale
ts_emb = ts_model(x_ts)
tab_emb = tab_model(x_tab, return_embedding=True)

print(f"TS embedding norm: {ts_emb.norm(dim=1).mean():.3f}")
print(f"Tab embedding norm: {tab_emb.norm(dim=1).mean():.3f}")

# If very different, add normalization before fusion
ts_emb = F.normalize(ts_emb, dim=1)
tab_emb = F.normalize(tab_emb, dim=1)
```

### Issue: Regime detection not working

**Solution**: Check meta-features
```python
meta = outputs["meta_features"]

# Should see variation
print(f"Volatility std: {meta[:, 0].std():.3f}")  # Should be > 0.01
print(f"Trend std: {meta[:, 1].std():.3f}")       # Should be > 0.001

# If all similar, increase meta_window
fusion = AdvancedFusionModule(..., meta_window=48)  # Instead of 24
```

---

## Next Steps

1. **Read full docs**: [FUSION.md](FUSION.md)
2. **Run example**: `python example_fusion.py`
3. **Run tests**: `pytest tests/test_fusion.py -v`
4. **Experiment**: Try different strategies on your data

---

**Version**: 2.4.0
**Module**: `models/fusion.py`
**Full Documentation**: [FUSION.md](FUSION.md)
